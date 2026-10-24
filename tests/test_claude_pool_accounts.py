"""Opt-in Hermes-pool Anthropic cards (``claudePoolAccounts``), all synthetic.

Grouping is Agent Monitor's (panel.py): credentials of one account collapse into one card; identity comes from
the OAuth profile endpoint. No network: the usage request and the profile request are fakes. No real Hermes
home: pool rows, settings and the shared state directory are injected.
"""
import importlib
import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_claude_multi_account import _cache_module  # noqa: E402

# One package for fetcher and cache, so QuotaAccount isinstance checks agree.
quota_cache, _base = _cache_module()
b = importlib.import_module("quota_plugin_under_test.quota_providers.builtin")
cp = importlib.import_module("quota_plugin_under_test.quota_providers.claude_pool")

NOW = time.time()
FUTURE_MS = int((NOW + 3 * 3600) * 1000)
PAST_MS = int((NOW - 60) * 1000)


def usage(session, week):
    return {"five_hour": {"utilization": session, "resets_at": "2026-10-14T12:00:00Z"},
            "seven_day": {"utilization": week, "resets_at": "2026-10-17T10:00:00Z"}}


def row(rid, label, token, priority, expires=FUTURE_MS, source="manual:hermes_pkce"):
    return {"id": rid, "label": label, "access_token": token, "refresh_token": "never-read",
            "priority": priority, "expires_at_ms": expires, "source": source}


class PoolAccountsTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.payloads = {"tok-a": usage(20, 60), "tok-b": usage(5, 30), "tok-c": usage(50, 10),
                         "tok-cc": usage(20, 60)}
        self.ids = {"tok-a": "key-a", "tok-cc": "key-a", "tok-b": "key-b", "tok-c": "key-c"}
        self.slots = {}

        def fake_request(token, *, timeout=15.0):
            self.calls.append(token)
            payload = self.payloads.get(token)
            return (payload, None) if payload is not None else (None, "rate-limited")

        def fake_identity(token, slot, *, expired, deadline, state, updates):
            if expired:
                return self.slots.get(slot)
            return self.ids.get(token)

        patches = [
            mock.patch.object(b, "_request_anthropic_usage", side_effect=fake_request),
            mock.patch.object(b, "_anthropic_identity", side_effect=fake_identity),
            mock.patch.object(b, "_load_identities", return_value={}),
            mock.patch.object(b, "_save_identities"),
            mock.patch.object(b, "_core_anthropic_is_oauth", return_value=True),
            mock.patch.object(b, "_claude_accounts_setting", return_value=([], False)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fetch(self, rows, *, enabled=True, primary="tok-a", strategy=None, entries=None, identity=None):
        settings = {cp.SETTING: enabled, "__strategy__": strategy}
        if identity is not None:
            settings[cp.IDENTITY_SETTING] = identity
        with mock.patch.object(cp, "_settings", return_value=settings), \
                mock.patch.object(cp, "pool_rows", return_value=rows), \
                mock.patch.object(b, "_core_anthropic_token", return_value=primary):
            if entries is not None:
                with mock.patch.object(b, "_claude_accounts_setting", return_value=(entries, True)), \
                        mock.patch.object(b, "_read_claude_account_token", return_value=("tok-c", None)), \
                        mock.patch.object(b, "_claude_subscription_type", return_value=None):
                    return b._fetch_anthropic()
            return b._fetch_anthropic()

    def rows(self, result):
        return quota_cache._expand_accounts("anthropic", quota_cache._result_to_record(result))

    def test_off_by_default_keeps_the_single_card(self):
        result = self.fetch([row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)], enabled=False)
        self.assertEqual(result.accounts, [])
        self.assertEqual(self.calls, ["tok-a"])
        self.assertNotIn("pool", quota_cache._result_to_record(result))

    def test_string_false_is_not_consent(self):
        result = self.fetch([row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)], enabled="false")
        self.assertEqual(result.accounts, [])

    def test_one_card_per_account_in_pool_order(self):
        rows = [row("bb2", "Account B", "tok-b", 1), row("aa1", "Account A", "tok-a", 0)]
        cache = self.rows(self.fetch(rows))
        self.assertEqual(list(cache), ["anthropic", "anthropic:pool-bb2"])
        primary, extra = cache["anthropic"], cache["anthropic:pool-bb2"]
        self.assertEqual(primary["account_label"], "Account A")
        self.assertEqual(primary["pool"], {"priority": 1, "size": 2, "strategy": "fill_first",
                                           "credentials": 1, "unverified": 0})
        self.assertEqual(extra["account_label"], "Account B")
        self.assertEqual(extra["pool"]["priority"], 2)
        self.assertEqual([w["used_percent"] for w in extra["windows"]], [5.0, 30.0])
        self.assertEqual(self.calls, ["tok-a", "tok-b"])  # sequential, one request per account

    def test_credentials_of_one_account_collapse_and_claude_code_is_not_the_name(self):
        rows = [row("cc0", "claude_code", "tok-cc", 0, source="claude_code"),
                row("aa1", "Account A", "tok-a", 1), row("bb2", "Account B", "tok-b", 2)]
        cache = self.rows(self.fetch(rows, primary="tok-cc"))
        self.assertEqual(list(cache), ["anthropic", "anthropic:pool-bb2"])
        self.assertEqual(cache["anthropic"]["account_label"], "Account A")
        self.assertEqual(cache["anthropic"]["pool"]["credentials"], 2)
        self.assertEqual(cache["anthropic"]["pool"]["priority"], 1)
        self.assertEqual(self.calls, ["tok-cc", "tok-b"])  # one read per account

    def test_next_credential_of_an_account_is_tried_when_the_first_fails(self):
        self.payloads["tok-cc"] = None  # rate limited
        rows = [row("cc0", "claude_code", "tok-cc", 0, source="claude_code"), row("aa1", "Account A", "tok-a", 1)]
        cache = self.rows(self.fetch(rows, primary="tok-cc"))
        self.assertIsNone(cache["anthropic"]["unavailable_reason"])
        self.assertEqual(self.calls, ["tok-cc", "tok-a"])

    def test_expired_token_is_never_sent_and_joins_its_remembered_account(self):
        self.slots["pool-aa9"] = "key-a"
        self.slots["pool-bb2"] = "key-b"
        rows = [row("aa1", "Account A", "tok-a", 0), row("aa9", "Old A", "tok-old", 1, expires=PAST_MS),
                row("bb2", "Account B", "tok-b", 2, expires=PAST_MS)]
        cache = self.rows(self.fetch(rows))
        self.assertEqual(cache["anthropic"]["pool"]["credentials"], 2)
        extra = cache["anthropic:pool-bb2"]
        self.assertEqual(extra["unavailable_reason"], "token-expired")
        self.assertIn("Hermes refreshes it on next use", extra["details"][0])
        self.assertEqual(self.calls, ["tok-a"])

    def test_unknown_identity_is_listed_unverified_not_its_own_card(self):
        self.ids.pop("tok-b")
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        cache = self.rows(self.fetch(rows))
        self.assertEqual(list(cache), ["anthropic"])
        self.assertEqual(cache["anthropic"]["pool"]["unverified"], 1)
        self.assertEqual(self.calls, ["tok-a"])  # unknown accounts are read only when nothing else was

    def test_unknown_identities_read_once_when_no_account_is_known(self):
        self.ids = {}
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        cache = self.rows(self.fetch(rows))
        self.assertEqual(list(cache), ["anthropic"])
        self.assertEqual(cache["anthropic"]["pool"]["unverified"], 1)
        self.assertEqual(self.calls, ["tok-a"])

    def test_nothing_readable_gives_one_plain_card(self):
        self.ids = {}
        self.payloads = {}
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        cache = self.rows(self.fetch(rows))
        self.assertEqual(list(cache), ["anthropic"])
        self.assertEqual(cache["anthropic"]["unavailable_reason"], "rate-limited")

    def test_identity_off_groups_by_token_only(self):
        rows = [row("cc0", "claude_code", "tok-cc", 0, source="claude_code"), row("aa1", "Account A", "tok-a", 1)]
        cache = self.rows(self.fetch(rows, primary="tok-cc", identity=False))
        self.assertEqual(list(cache), ["anthropic", "anthropic:pool-aa1"])
        b._anthropic_identity.assert_not_called()

    def test_cache_never_holds_tokens_keys_or_sources(self):
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1, source="claude_code")]
        text = repr(self.rows(self.fetch(rows)))
        for secret in ("tok-a", "tok-b", "never-read", "claude_code", "hermes_pkce", "expires", "key-a", "key-b"):
            self.assertNotIn(secret, text)

    def test_claude_accounts_entries_are_deduplicated_by_token(self):
        rows = [row("aa1", "Account A", "tok-a", 0), row("cc3", "Account C", "tok-c", 1)]
        entries = [{"id": "work", "label": "Work", "config_dir": "/synthetic/claude-work"}]
        cache = self.rows(self.fetch(rows, entries=entries))
        self.assertEqual(list(cache), ["anthropic", "anthropic:pool-cc3"])
        self.assertEqual(self.calls, ["tok-a", "tok-c"])

    def test_spent_deadline_marks_accounts_timeout(self):
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        with mock.patch.object(b, "_ANTHROPIC_MULTI_BUDGET_S", 0.0):
            cache = self.rows(self.fetch(rows))
        self.assertEqual(cache["anthropic:pool-bb2"]["unavailable_reason"], "timeout")
        self.assertEqual(self.calls, [])

    def test_strategy_comes_from_the_profile_config(self):
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        cache = self.rows(self.fetch(rows, strategy="round_robin"))
        self.assertEqual(cache["anthropic:pool-bb2"]["pool"]["strategy"], "round_robin")

    def test_primary_outside_the_pool_keeps_every_pool_account(self):
        self.payloads["tok-env"] = usage(1, 1)
        self.ids["tok-env"] = "key-env"
        rows = [row("aa1", "Account A", "tok-a", 0), row("bb2", "Account B", "tok-b", 1)]
        cache = self.rows(self.fetch(rows, primary="tok-env"))
        self.assertEqual(list(cache), ["anthropic", "anthropic:pool-aa1", "anthropic:pool-bb2"])
        self.assertNotIn("pool", cache["anthropic"])


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self, n):
        return self.body


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(b, "_backoff_state_path", return_value=str(Path(self.tmp.name) / "backoff.json"))
        p.start()
        self.addCleanup(p.stop)

    def ask(self, token, slot="pool-x", expired=False):
        state = b._load_identities()
        updates = {}
        key = b._anthropic_identity(token, slot, expired=expired, deadline=b.Deadline(5), state=state, updates=updates)
        b._save_identities(updates)
        return key

    def test_key_is_cached_per_token_and_state_holds_no_secrets(self):
        body = json.dumps({"account": {"uuid": "uuid-synthetic-1", "email_address": "x@example.invalid"}}).encode()
        with mock.patch.object(b, "urlopen_no_redirect", return_value=Resp(body)) as net:
            k1 = self.ask("tok-synthetic")
            k2 = self.ask("tok-synthetic")
        self.assertTrue(k1)
        self.assertEqual(k1, k2)
        self.assertEqual(net.call_count, 1)
        self.assertEqual(net.call_args[0][0].full_url, b._ANTHROPIC_PROFILE_URL)
        text = (Path(self.tmp.name) / b._ANTHROPIC_IDENTITY_FILENAME).read_text()
        for secret in ("tok-synthetic", "uuid-synthetic-1", "example.invalid"):
            self.assertNotIn(secret, text)

    def test_refused_is_not_repeated_and_429_opens_the_cooldown(self):
        e403 = urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b""))
        with mock.patch.object(b, "urlopen_no_redirect", side_effect=e403) as net:
            self.assertIsNone(self.ask("tok-setup"))
            self.assertIsNone(self.ask("tok-setup"))
        self.assertEqual(net.call_count, 1)
        e429 = urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b""))
        with mock.patch.object(b, "urlopen_no_redirect", side_effect=e429) as net:
            self.assertIsNone(self.ask("tok-busy"))
            self.assertIsNone(self.ask("tok-busy"))
        self.assertEqual(net.call_count, 1)  # the second call waits for the cooldown
        self.assertTrue(b._anthropic_cooling_down("tok-busy"))

    def test_expired_token_uses_the_slot_memory_without_a_request(self):
        body = json.dumps({"account": {"uuid": "uuid-synthetic-2"}}).encode()
        with mock.patch.object(b, "urlopen_no_redirect", return_value=Resp(body)):
            key = self.ask("tok-old", slot="pool-a")
        with mock.patch.object(b, "urlopen_no_redirect") as net:
            self.assertEqual(self.ask("tok-new-but-expired", slot="pool-a", expired=True), key)
        net.assert_not_called()


class CredentialParsingTests(unittest.TestCase):
    def test_labels_are_sanitized_and_generic_labels_fall_back(self):
        rows = [row("x1", "  Bad\u202eLabel\x07 ", "t1", 0), row("x2", "hermes_pkce", "t2", 1),
                row("x3", "", "t3", 2, source="claude_code"), {"id": "bad id!", "access_token": "t4"}]
        creds = cp.credentials(now=NOW, rows=rows)
        self.assertEqual([c["label"] for c in creds], ["BadLabel", "Account 2", "Claude Code", "Account 4"])
        self.assertEqual([c["id"] for c in creds], ["pool-x1", "pool-x2", "pool-x3", "pool-4"])
        self.assertEqual([c["named"] for c in creds], [True, False, False, False])

    def test_priority_order_skew_and_bounds(self):
        rows = [row("late", "L", "t9", 5), row("first", "F", "t1", 0),
                row("soon", "S", "t2", 1, expires=int((NOW + 30) * 1000)),
                {"id": "nopri", "label": "N", "access_token": "t3"},
                {"id": "empty", "label": "E", "access_token": "  "}]
        creds = cp.credentials(now=NOW, rows=rows)
        self.assertEqual([c["id"] for c in creds], ["pool-first", "pool-soon", "pool-late", "pool-nopri"])
        self.assertEqual([c["expired"] for c in creds], [False, True, False, False])
        many = [row("r%d" % i, "R%d" % i, "tok%d" % i, i) for i in range(20)]
        self.assertEqual(len(cp.credentials(now=NOW, rows=many)), cp.MAX_POOL_ROWS)

    def test_strategy_and_identity_settings(self):
        self.assertEqual(cp.strategy({"__strategy__": None}), "fill_first")
        self.assertEqual(cp.strategy({"__strategy__": "bogus"}), "fill_first")
        self.assertEqual(cp.strategy({"__strategy__": "random"}), "random")
        self.assertTrue(cp.identity_enabled({}))
        self.assertFalse(cp.identity_enabled({cp.IDENTITY_SETTING: False}))


if __name__ == "__main__":
    unittest.main()
