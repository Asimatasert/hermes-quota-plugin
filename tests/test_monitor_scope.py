"""Opt-in ``monitorScope: all`` (default ``active``): every profile's open sessions + Claude Code / Copilot CLI.

Synthetic homes only. The hardened default stays exactly as upstream 3.0 ships it (see test_monitor_trial_policy).
"""
from __future__ import annotations

import json
import time
import unittest

from monitor_fixtures import Home, iso, load

live = load("quota_monitor.live")
clis = load("quota_monitor.clis")
trial = load("quota_monitor.trial")
common = load("quota_monitor.common")


def config(scope=None, enrolled=True):
    lines = ["plugins:", "  entries:", "    quota:", "      settings:",
             "        monitorEnabled: %s" % ("true" if enrolled else "false")]
    if scope is not None:
        lines.append("        monitorScope: %s" % scope)
    return "\n".join(lines) + "\n"


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(profiles=["work"])
        self.addCleanup(self.home.close)
        now = time.time()
        self.now = now
        self.home.session("s-default", "cron", started=now - 600, last=now - 20)
        self.home.session("s-work", "cron", "work", started=now - 600, last=now - 20)
        project = self.home.cli_home / ".claude" / "projects" / "p1"
        project.mkdir(parents=True)
        (project / "sess.jsonl").write_text(json.dumps({
            "type": "assistant", "timestamp": iso(now - 5), "sessionId": "external-1",
            "message": {"model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5},
                        "content": []}}) + "\n")

    def ids(self):
        out = live.live(common.profile_homes(), self.now, record_sample=False)
        return out, {(r.get("kind"), r.get("profile"), r["id"]) for r in out["running"]}

    def test_default_is_active_profile_only_without_external_clis(self):
        self.home.write("config.yaml", config())
        out, ids = self.ids()
        self.assertEqual(out["scope"], "active")
        self.assertIn(("hermes", "default", "s-default"), ids)
        self.assertNotIn(("hermes", "work", "s-work"), ids)
        self.assertFalse(any(k == "claude-code" for k, _p, _i in ids))
        self.assertFalse(trial.external_cli_collection())

    def test_scope_all_adds_every_profile_and_claude_code(self):
        self.home.write("config.yaml", config("all"))
        out, ids = self.ids()
        self.assertEqual(out["scope"], "all")
        self.assertIn(("hermes", "default", "s-default"), ids)
        self.assertIn(("hermes", "work", "s-work"), ids)
        self.assertTrue(any(k == "claude-code" for k, _p, _i in ids), ids)
        self.assertTrue(trial.external_cli_collection())

    def test_only_the_literal_all_with_enrollment_widens(self):
        for value in ('"ALL"', "true", "1", "everything"):
            self.home.write("config.yaml", config(value))
            self.assertEqual(trial.monitor_scope(), "active", value)
        self.home.write("config.yaml", config("all", enrolled=False))
        self.assertEqual(trial.monitor_scope(), "active")

    def test_scope_never_widens_kanban_metadata_or_tools(self):
        self.home.write("config.yaml", config("all"))
        self.assertFalse(trial.SHARED_KANBAN)
        self.assertFalse(trial.ACCOUNT_METADATA)
        self.assertFalse(trial.AGENT_TOOLS)
        self.assertEqual(live.kanban_cards(), {})


if __name__ == "__main__":
    unittest.main()
