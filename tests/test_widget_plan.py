"""Widget: Plan tab, reset times in both pane-detail modes (#77), pool pills and the pinned "Now" block.

SYNTHETIC payloads only; the plan payload is produced by the real backend planner from a synthetic cache.
"""
import json
import unittest

from monitor_fixtures import load
from test_widget_i18n import LOCALES, strings
from test_widget_monitor import NOW, find, iso, quota_payload, run, snapshot
from widget_harness import text

plan_mod = load("quota_monitor.plan")
H = 3600


def plan_payload(two=True, degraded=False):
    from datetime import timezone
    items = [{"provider": "anthropic", "base": "anthropic", "name": "anthropic", "account_label": "Account A",
              "windows": [{"label": "Current session", "used": 20.0, "reset": NOW + 3 * H},
                          {"label": "Current week", "used": 60.0, "reset": NOW + 28 * H}],
              "unavailable": None, "pool": {"priority": 1, "size": 2, "strategy": "fill_first"}}]
    if two:
        items.append({"provider": "anthropic:pool-b", "base": "anthropic", "name": "anthropic · Account B",
                      "account_label": "Account B",
                      "windows": [{"label": "Current session", "used": 0.0, "reset": None},
                                  {"label": "Current week", "used": 10.0, "reset": NOW + 50 * H}],
                      "unavailable": None, "pool": {"priority": 2, "size": 2, "strategy": "fill_first"}})
    out = plan_mod.build_plan(items, [], NOW, timezone.utc, history=not degraded)
    out.update(monitor_version=1, generated_at=NOW, enrolled=not degraded, degraded=degraded, errors={})
    return json.loads(json.dumps(out))


def pooled_quota():
    data = quota_payload()
    data["providers"]["anthropic"]["pool"] = {"priority": 1, "size": 2, "strategy": "fill_first"}
    data["providers"]["anthropic:home"]["pool"] = {"priority": 2, "size": 2, "strategy": "fill_first"}
    return data


class ResetVisibilityTests(unittest.TestCase):
    def test_reset_shows_in_clean_and_dense_with_absolute_on_hover(self):
        for mode in ("clean", "dense"):
            out = run(component="pane", data=quota_payload(), storage={"paneDetail": mode, "monitorEnabled": False})
            resets = find(out["tree"], lambda n: n.get("props", {}).get("data-window-reset"))
            self.assertEqual(len(resets), 2, mode)  # the selected card of the account group
            self.assertTrue(all("(" in r["props"]["title"] for r in resets), mode)  # "in 2h (Today 14:00)"

    def test_status_bar_card_window_hover_has_absolute_reset(self):
        out = run(expr='QuotaCardWindow({w: {label: "Current session", used_percent: 40, reset_at: %s}, nowMs: %d})'
                  % (json.dumps(iso(7200)), NOW * 1000))
        self.assertIn("(", out["value"]["props"]["title"])


class PoolPillTests(unittest.TestCase):
    def test_pool_position_and_tried_first(self):
        out = run(component="pane", data=pooled_quota(), storage={"monitorEnabled": False})
        pills = find(out["tree"], lambda n: n.get("props", {}).get("data-pool-position") is not None)
        self.assertTrue(pills)
        self.assertIn("tried first", text(pills[0]))


class PlanViewTests(unittest.TestCase):
    def render(self, **kw):
        storage = {"paneView": "plan", "monitorEnabled": kw.pop("monitor", True)}
        storage.update(kw.pop("storage", {}))
        return run(component="pane", data=quota_payload(), monitor=snapshot(), plan=kw.pop("plan", plan_payload()),
                   storage=storage, enroll=False, **kw)

    def test_headline_answers_the_question_in_one_sentence(self):
        out = self.render()
        heads = find(out["tree"], lambda n: n.get("props", {}).get("data-plan-headline"))
        self.assertEqual(len(heads), 2)
        first = text(heads[0])
        self.assertTrue(first.startswith("6 sessions left until "), first)
        self.assertIn("6.7% of the week per session", first)
        self.assertIn("Current session: 20% used", first)
        self.assertIn("budget = week left 40% ÷ 6 sessions", heads[0]["props"]["title"])

    def test_multi_account_card_and_pool_pills(self):
        out = self.render()
        multi = find(out["tree"], lambda n: n.get("props", {}).get("data-plan-multi"))
        self.assertTrue(multi)
        self.assertIn("Use Anthropic · Account A now", text(multi[0]))
        self.assertTrue(find(out["tree"], lambda n: n.get("props", {}).get("data-plan-chart")))

    def test_degraded_notice_without_the_monitor(self):
        out = self.render(monitor=False, plan=plan_payload(two=False, degraded=True))
        self.assertTrue(find(out["tree"], lambda n: n.get("props", {}).get("data-plan-degraded")))
        tabs = find(out["tree"], lambda n: n.get("props", {}).get("data-tabs"))
        self.assertEqual(tabs[0]["props"]["data-tabs"], "quota,plan")

    def test_plan_tab_can_be_switched_off(self):
        out = self.render(monitor=False, storage={"planTab": False})
        self.assertFalse(find(out["tree"], lambda n: n.get("props", {}).get("data-plan-headline")))

    def test_every_locale_renders_without_raw_keys(self):
        for locale in LOCALES:
            with self.subTest(locale=locale):
                out = self.render(locale=locale)
                shown = " ".join(strings(out["tree"]))
                for key in ("planHeadline", "planBudget", "planFormula", "poolPosition", "planPace", "nowTitle"):
                    self.assertNotIn(key, shown)
                self.assertNotIn("undefined", shown)


class NowBlockTests(unittest.TestCase):
    def test_live_view_pins_the_focused_chat_and_the_account_in_use(self):
        snap = snapshot()
        focus_id = snap["live"]["running"][0]["id"]
        out = run(component="pane", data=pooled_quota(), monitor=snap,
                  storage={"paneView": "live"}, focus={"storedId": focus_id})
        now = find(out["tree"], lambda n: n.get("props", {}).get("data-now"))
        self.assertEqual(len(now), 1)
        self.assertIn("Claude account in use: Anthropic · work", text(now[0]))
        # The focused row is not repeated below.
        self.assertEqual(text(out["tree"]).count(snap["live"]["running"][0]["title"]), 1)

    def test_agents_card_has_the_now_block(self):
        snap = snapshot()
        focus_id = snap["live"]["running"][0]["id"]
        out = run(expr="AgentsCard({snapshot: %s, scope: null, close() {}})" % json.dumps(snap),
                  data=pooled_quota(), focus={"storedId": focus_id})
        self.assertTrue(find(out["value"], lambda n: n.get("props", {}).get("data-now")))

    def test_exhausted_first_account_moves_the_pick_to_the_next(self):
        data = pooled_quota()
        data["providers"]["anthropic"]["windows"][0]["used_percent"] = 100
        out = run(expr="anthropicInUse(%s).pid" % json.dumps(data["providers"]))
        self.assertEqual(out["value"], "anthropic:home")


if __name__ == "__main__":
    unittest.main()
