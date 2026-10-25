"""Opt-in ``plugins.entries.quota.settings.fullMode`` (literal ``true`` only, default off).

Off (absent, false, "true", 1, ...) must behave exactly like the upstream read-only trial: no credential
store read, no account/budget write path, no Kanban read, no monitor agent tools, active profile only.
On, every trial gate opens: pool metadata (safe subset only), account order/strategy writes through Hermes'
own CLI with a fixed argv, the audit table, budget writes, Kanban reads, the seven agent tools and every
profile of the root. Synthetic homes and a fake Hermes CLI only; nothing here runs the real CLI.
"""

from __future__ import annotations

import json
import os
import stat
import time
import unittest
from unittest import mock

from monitor_fixtures import Home, load

trial = load("quota_monitor.trial")
accounts = load("quota_monitor.accounts")
common = load("quota_monitor.common")
qm = load("quota_monitor")
store = load("quota_monitor.store")
tools = load("quota_monitor.tools")
usage = load("quota_monitor.usage")
health = load("quota_monitor.health")

SECRET_BITS = ("SYNTH-ACCESS", "SYNTH-REFRESH", "sk-synthetic", "refresh_token", "access_token")
MONITOR_TOOLS = {"get_quota_status", "get_model_health", "get_fallback_chains", "get_budget",
                 "get_running_counts", "get_agent_attention", "recommend_model"}


def _row(rid, label, priority, **extra):
    row = {"id": rid, "label": label, "auth_type": "oauth", "priority": priority, "source": "manual",
           "access_token": "SYNTH-ACCESS-" + rid, "refresh_token": "SYNTH-REFRESH-" + rid,
           "base_url": "https://synthetic.invalid/v1", "request_count": 1}
    row.update(extra)
    return row


def config(full=None, enrolled=True, extra=""):
    lines = ["plugins:", "  entries:", "    quota:", "      settings:",
             "        monitorEnabled: %s" % ("true" if enrolled else "false")]
    if full is not None:
        lines.append("        fullMode: %s" % full)
    return "\n".join(lines) + "\n" + extra


class FakeHermes:
    """Records argv and applies the change like `hermes auth priority` / `hermes config set` would."""

    def __init__(self, home: Home):
        self.home, self.calls = home, []

    def __call__(self, argv, env):
        self.calls.append(list(argv))
        assert argv[0] == "--profile"
        target = self.home.homes[argv[1]]
        if argv[2:4] == ["auth", "priority"]:
            provider, rid, prio = argv[4], argv[5], int(argv[6])
            path = target / "auth.json"
            data = json.loads(path.read_text())
            rows = sorted(data["credential_pool"][provider], key=lambda r: r["priority"])
            moving = next(r for r in rows if r["id"] == rid)
            rows.remove(moving)
            rows.insert(prio, moving)
            for i, r in enumerate(rows):
                r["priority"] = i
            data["credential_pool"][provider] = rows
            path.write_text(json.dumps(data))
        elif argv[2:4] == ["config", "set"]:
            key, value = argv[4], argv[5]
            path = target / "config.yaml"
            text = path.read_text() if path.exists() else ""
            provider = key.split(".", 1)[1]
            text += "credential_pool_strategies:\n  %s: %s\n" % (provider, value)
            path.write_text(text)
        return 0, "ok"


class _Base(unittest.TestCase):
    def setUp(self):
        self.home = Home(profiles=["work"])
        self.addCleanup(self.home.close)
        self.home.write("auth.json", json.dumps({"version": 1, "credential_pool": {
            "anthropic": [_row("aa11bb", "acct-a", 0), _row("cc22dd", "acct-b", 1), _row("ee33ff", "acct-c", 2)],
        }}))
        self.home.card("k1", "running", started=time.time() - 60)
        self.set_config(None)

    def set_config(self, full, enrolled=True):
        self.home.write("config.yaml", config(full, enrolled))
        self.reset_caches()

    @staticmethod
    def reset_caches():
        trial._full_cache.update(at=0.0, home=None, value=False)
        trial._scope_cache.update(at=0.0, home=None, value=trial.SCOPE_ACTIVE)


class FullModeOffTests(_Base):
    """Absent / false / non-literal: identical to the upstream read-only trial."""

    def assert_upstream(self):
        self.assertFalse(trial.full_mode())
        for gate in (trial.account_metadata, trial.shared_kanban, trial.agent_tools, trial.account_mutations,
                     trial.budget_writes, trial.all_profiles, trial.external_cli_collection):
            self.assertFalse(gate(), gate.__name__)
        payload = qm.accounts_view()
        self.assertEqual(payload["pools"], [])
        self.assertEqual(payload["metadata"], {"available": False, "reason": trial.METADATA_REASON})
        self.assertFalse(payload["capabilities"]["set_priority"])
        self.assertFalse(payload["capabilities"]["set_strategy"])
        self.assertNotIn("full_mode", payload)
        self.assertNotIn("recent_changes", payload)
        self.assertEqual([p["profile"] for p in payload["profiles"]], ["default"])
        self.assertNotIn("full_mode", qm.snapshot(record_sample=False))
        self.assertEqual(common.profile_homes(), [("default", self.home.root)])
        self.assertEqual(usage.kanban_glance(self.home.root)["available"], False)
        self.assertEqual(health.read_pools(), {})

    def test_absent_setting_is_upstream_behaviour(self):
        self.assert_upstream()

    def test_non_literal_values_are_off(self):
        for value in ("false", '"true"', "1", "yes", "on", "[true]"):
            with self.subTest(value=value):
                self.set_config(value)
                self.assert_upstream()

    def test_full_mode_without_monitor_enrollment_collects_nothing(self):
        self.set_config("true", enrolled=False)
        self.assertTrue(trial.full_mode())
        self.assertEqual(qm.accounts_view()["error_code"], "monitor-enrollment-off")
        self.assertEqual(qm.set_budget(daily=1)["error_code"], "monitor-enrollment-off")
        self.assertEqual(json.loads(tools.get_quota_status({}))["error_code"], "monitor-enrollment-off")

    def test_writes_refuse_before_any_state_read_or_child_process(self):
        runner = mock.Mock(side_effect=AssertionError("CLI spawned"))
        with mock.patch.object(accounts, "accounts_payload", side_effect=AssertionError("state read")):
            for fn, args in ((accounts.set_priority, ("anthropic", "ee33ff", 0, "default")),
                             (accounts.set_strategy, ("anthropic", "random", "default"))):
                with self.assertRaises(accounts.AccountsError) as cm:
                    fn(*args, runner=runner)
                self.assertEqual(cm.exception.code, "trial-read-only")
        runner.assert_not_called()

    def test_budget_write_is_refused_and_writes_nothing(self):
        out = qm.set_budget(daily=5, monthly=50)
        self.assertEqual(out["error_code"], "trial-read-only")
        self.assertFalse(store.prefs_path().exists())
        with self.assertRaises(trial.TrialError):
            store.write_prefs({"budget": {"daily": 1}})
        self.assertFalse(store.prefs_path().exists())

    def test_no_monitor_tools_register_and_direct_calls_are_blocked(self):
        ctx = mock.Mock()
        self.assertEqual(tools.register_tools(ctx), [])
        ctx.register_tool.assert_not_called()
        self.assertEqual(json.loads(tools.get_quota_status({}))["error_code"], "trial-tools-disabled")


class FullModeOnTests(_Base):
    def setUp(self):
        super().setUp()
        self.set_config("true")
        self.fake = FakeHermes(self.home)

    def test_every_gate_opens(self):
        for gate in (trial.full_mode, trial.account_metadata, trial.shared_kanban, trial.agent_tools,
                     trial.account_mutations, trial.budget_writes, trial.all_profiles,
                     trial.external_cli_collection):
            self.assertTrue(gate(), gate.__name__)
        self.assertEqual(trial.monitor_scope(), trial.SCOPE_ALL)
        self.assertEqual([n for n, _ in common.profile_homes()], ["default", "work"])

    def test_accounts_payload_lists_pools_without_secrets(self):
        payload = qm.accounts_view()
        self.assertTrue(payload["full_mode"])
        self.assertEqual(payload["metadata"], {"available": True, "reason": None})
        self.assertTrue(payload["capabilities"]["set_priority"])
        self.assertTrue(payload["capabilities"]["set_strategy"])
        pool = next(p for p in payload["pools"] if p["provider"] == "anthropic" and p["owner"] == "default")
        self.assertEqual([r["id"] for r in pool["rows"]], ["aa11bb", "cc22dd", "ee33ff"])
        self.assertEqual(sorted(pool["used_by"]), ["default", "work"])
        self.assertTrue(pool["shared"] and pool["writable"])
        self.assertEqual(payload["recent_changes"], [])
        text = json.dumps(payload)
        for bit in SECRET_BITS + ("synthetic.invalid",):
            self.assertNotIn(bit, text)
        self.assertTrue(qm.snapshot(record_sample=False)["full_mode"])

    def test_set_priority_runs_the_fixed_official_argv_and_audits(self):
        before = (self.home.root / "auth.json").read_bytes()
        res = accounts.set_priority("anthropic", "ee33ff", 0, profile="default", runner=self.fake)
        self.assertEqual(self.fake.calls, [["--profile", "default", "auth", "priority", "anthropic", "ee33ff", "0"]])
        self.assertEqual(res["command"], "hermes --profile default auth priority anthropic ee33ff 0")
        self.assertEqual((res["before"], res["after"], res["changed"]),
                         (["aa11bb", "cc22dd", "ee33ff"], ["ee33ff", "aa11bb", "cc22dd"], True))
        self.assertNotEqual((self.home.root / "auth.json").read_bytes(), before)  # only the fake CLI wrote it
        audit = qm.accounts_view()["recent_changes"]
        self.assertEqual(audit[0]["action"], "set-priority")
        self.assertEqual(audit[0]["detail"]["after"], ["ee33ff", "aa11bb", "cc22dd"])
        self.assertNotIn("SYNTH", json.dumps(audit))

    def test_set_priority_validation_never_spawns(self):
        for args, code in (((("anthropic", "nope", 0, "default")), "unknown-target"),
                           ((("anthropic", "acct-a", 0, "default")), "unknown-target"),
                           ((("anthropic", "ee33ff", 3, "default")), "bad-priority"),
                           ((("anthropic", "ee33ff", True, "default")), "bad-priority"),
                           ((("anthropic", "ee33ff", 0, "../x")), "bad-profile"),
                           ((("anthropic", "ee33ff", 0, "ghost")), "unknown-profile"),
                           ((("bad provider!", "ee33ff", 0, "default")), "bad-provider"),
                           ((("anthropic", "ee33ff", 0, "work")), "borrowed-pool")):
            with self.subTest(args=args):
                with self.assertRaises(accounts.AccountsError) as cm:
                    accounts.set_priority(*args, runner=self.fake)
                self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.fake.calls, [])

    def test_set_strategy_targets_one_profile(self):
        res = accounts.set_strategy("anthropic", "round_robin", profile="work", runner=self.fake)
        self.assertEqual(self.fake.calls, [["--profile", "work", "config", "set",
                                            "credential_pool_strategies.anthropic", "round_robin"]])
        self.assertEqual((res["before"], res["after"], res["affected_profiles"]), ("fill_first", "round_robin", ["work"]))

    def test_cli_failure_is_scrubbed(self):
        bad = lambda argv, env: (1, "boom sk-" + "s" * 30)  # noqa: E731
        out = qm._write_result(lambda: accounts.set_priority("anthropic", "ee33ff", 0, "default", bad))
        self.assertEqual(out["error_code"], "cli-failed")
        self.assertNotIn("s" * 30, out["error"])

    def test_budget_write_round_trip_is_private(self):
        out = qm.set_budget(daily=5, monthly=50)
        self.assertEqual(out["budget"], {"daily": 5.0, "monthly": 50.0})
        self.assertEqual(stat.S_IMODE(os.stat(store.prefs_path()).st_mode), 0o600)
        self.assertEqual(qm.set_budget(monthly=0)["budget"], {"daily": 5.0, "monthly": None})
        self.assertEqual(qm.set_budget(daily=-1)["error_code"], "bad-budget")
        self.assertEqual(qm.read_budget(), {"daily": 5.0, "monthly": None})
        leftovers = [p.name for p in store.prefs_path().parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_kanban_reads_open(self):
        glance = usage.kanban_glance(self.home.root)
        self.assertNotEqual(glance.get("available"), False)
        self.assertTrue(glance["boards"])

    def test_tools_register_and_recheck_the_setting_on_every_call(self):
        ctx = mock.Mock()
        names = set(tools.register_tools(ctx))
        self.assertTrue(MONITOR_TOOLS <= names)
        self.assertNotIn("error_code", json.loads(tools.get_quota_status({})))
        self.set_config("false")  # turned off without a restart: the registered handler refuses
        self.assertEqual(json.loads(tools.get_quota_status({}))["error_code"], "trial-tools-disabled")

    def test_turning_full_mode_off_closes_the_write_path_again(self):
        self.set_config(None)
        with self.assertRaises(accounts.AccountsError) as cm:
            accounts.set_priority("anthropic", "ee33ff", 0, profile="default", runner=self.fake)
        self.assertEqual(cm.exception.code, "trial-read-only")
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
