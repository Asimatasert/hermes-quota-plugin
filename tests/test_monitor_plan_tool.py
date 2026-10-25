"""The opt-in ``get_quota_plan`` agent tool: off by default, numbers and codes only (synthetic data)."""
from __future__ import annotations

import json
import unittest

from monitor_fixtures import Home, anthropic_record, iso, load, walk_keys

tools = load("quota_monitor.tools")
NOW_OFFSET_H = 3600.0


class Ctx:
    def __init__(self):
        self.registered = []

    def register_tool(self, **kwargs):
        self.registered.append(kwargs)


def settings_yaml(**values):
    lines = ["plugins:", "  entries:", "    quota:", "      settings:"]
    lines += ["        %s: %s" % (k, v) for k, v in values.items()]
    return "\n".join(lines) + "\n"


class PlanToolTests(unittest.TestCase):
    def setUp(self):
        import time
        self.home = Home()
        self.addCleanup(self.home.close)
        now = time.time()
        a = anthropic_record(20, 60, now + 3 * NOW_OFFSET_H, now + 28 * NOW_OFFSET_H)
        a.update(account_label="Secret Label A", pool={"priority": 1, "size": 3, "strategy": "fill_first"})
        b = anthropic_record(5, 10, now + 1 * NOW_OFFSET_H, now + 50 * NOW_OFFSET_H)
        b.update(provider="anthropic", account_label="Secret Label B",
                 pool={"priority": 2, "size": 3, "strategy": "fill_first"})
        c = {"label": "anthropic · Secret Label C", "provider": "anthropic", "account_label": "Secret Label C",
             "unavailable_reason": "Anthropic account limits are only available for OAuth-backed Claude accounts.",
             "details": ["/synthetic/path detail"], "windows": [],
             "pool": {"priority": 3, "size": 3, "strategy": "fill_first"}}
        self.home.quota_cache({"anthropic": a, "anthropic:pool-b2": b, "anthropic:pool-c3": c}, iso(now))

    def test_off_by_default_registers_nothing_and_refuses(self):
        ctx = Ctx()
        self.assertEqual(tools.register_tools(ctx), [])
        self.assertEqual(ctx.registered, [])
        self.assertEqual(json.loads(tools.get_quota_plan({}))["error_code"], "plan-tool-disabled")

    def test_string_true_is_not_consent(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled='"true"'))
        self.assertEqual(tools.register_tools(Ctx()), [])

    def test_enabled_registers_only_the_plan_tool(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled="true"))
        ctx = Ctx()
        self.assertEqual(tools.register_tools(ctx), ["get_quota_plan"])
        self.assertEqual(ctx.registered[0]["toolset"], tools.TOOLSET)
        self.assertEqual(ctx.registered[0]["schema"]["name"], "get_quota_plan")

    def test_answer_has_no_labels_paths_or_free_text(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled="true"))
        raw = tools.get_quota_plan({})
        out = json.loads(raw)
        for secret in ("Secret", "Label", "/synthetic", "OAuth", "pool-b2", "pool-c3", "summary"):
            self.assertNotIn(secret, raw)
        accounts = out["providers"][0]["accounts"]
        self.assertEqual([a["account"] for a in accounts], [1, 2, 3])
        self.assertEqual(accounts[2], {"account": 3, "plannable": False, "pool_position": 3, "reason": "unavailable"})
        self.assertEqual(accounts[0]["sessions_left_calendar"], 6)
        self.assertEqual(out["providers"][0]["multi"]["use_account"], 1)
        self.assertTrue(out["degraded"])  # monitor not enrolled: cache-only plan

        def leaves(o):
            if isinstance(o, dict):
                for v in o.values():
                    yield from leaves(v)
            elif isinstance(o, list):
                for v in o:
                    yield from leaves(v)
            else:
                yield o
        for value in leaves(out):
            self.assertTrue(value is None or isinstance(value, (bool, int, float))
                            or (isinstance(value, str) and len(value) <= 40 and " " not in value), value)
        self.assertNotIn("label", walk_keys(out))

    def test_provider_filter(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled="true"))
        self.assertEqual(json.loads(tools.get_quota_plan({"provider": "openai-codex"}))["providers"], [])

    def test_turning_it_off_later_refuses_calls_from_an_old_registry(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled="true"))
        ctx = Ctx()
        tools.register_tools(ctx)
        self.home.write("config.yaml", settings_yaml(planToolEnabled="false"))
        handler = ctx.registered[0]["handler"]
        self.assertEqual(json.loads(handler({}))["error_code"], "plan-tool-disabled")

    def test_monitor_tools_stay_unregistered_with_the_plan_tool_on(self):
        self.home.write("config.yaml", settings_yaml(planToolEnabled="true", monitorEnabled="true"))
        ctx = Ctx()
        self.assertEqual(tools.register_tools(ctx), ["get_quota_plan"])
        self.assertEqual(json.loads(tools.get_quota_status({}))["error_code"], "trial-tools-disabled")


if __name__ == "__main__":
    unittest.main()
