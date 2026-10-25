"""Quota pacing planner (``hermes quota monitor plan``) on SYNTHETIC histories only.

Covers: no history (defaults + calendar count), the user's worked example, a steady pace, a bursty pace that
runs the week out, a week reset in the middle of the history, two pool accounts, the active-hours setting and
the degraded (monitor off) payload.
"""
from __future__ import annotations

import json
import math
import sqlite3
import unittest
from datetime import timezone

from monitor_fixtures import Home, anthropic_record, iso, load

plan = load("quota_monitor.plan")
UTC = timezone.utc
H = 3600.0
NOW = 1_900_000_800.0  # synthetic, aligned to a whole hour in UTC
NOW = NOW - NOW % 3600


def item(provider="anthropic", s_used=20.0, s_reset=None, w_used=60.0, w_reset=None, pool=None,
         short="Current session", long="Current week", base="anthropic"):
    windows = []
    if short:
        windows.append({"label": short, "used": s_used, "reset": s_reset})
    if long:
        windows.append({"label": long, "used": w_used, "reset": w_reset})
    return {"provider": provider, "base": base, "name": provider, "account_label": None, "windows": windows,
            "unavailable": None, "pool": pool}


def synth_history(provider, *, r, start, end, week_reset, hours, fill, step=600.0, week_used0=0.0,
                  short="Current session", long="Current week"):
    """Readings every ``step`` s: a session starts at each listed UTC hour that is free and fills linearly
    to ``fill`` % over 3 h; the week rises by ``r`` per session point and resets every 7 days."""
    rows = []
    t = start
    s_used, s_reset = 0.0, None
    w_used = week_used0
    w_reset = week_reset
    while w_reset - 7 * 86400 > start:
        w_reset -= 7 * 86400
    s_start = start
    while t <= end:
        if t >= w_reset:
            w_used = 0.0
            w_reset += 7 * 86400
        if s_reset is not None and t >= s_reset:
            s_used, s_reset = 0.0, None
        hour = int((t % 86400) // 3600)
        if s_reset is None and hour in hours:
            s_reset = t + plan.SESSION_S
            s_start = t
        if s_reset is not None:
            target = min(fill, fill * (t - s_start) / (3 * H))
            if target > s_used:
                w_used = min(100.0, w_used + (target - s_used) * r)
                s_used = target
        rows.append((t, provider, short, round(s_used, 1), s_reset))
        rows.append((t, provider, long, float(math.floor(w_used)), w_reset))  # providers report whole %
        t += step
    return rows, s_used, s_reset, math.floor(w_used), w_reset


class NoHistoryTests(unittest.TestCase):
    def test_defaults_calendar_count_and_budget(self):
        acc = plan.plan_account(item(s_used=20, s_reset=NOW + 3 * H, w_used=60, w_reset=NOW + 28 * H),
                                [], NOW, UTC)
        self.assertTrue(acc["plannable"])
        # current session + ceil((28h - 3h) / 5h) = 1 + 5
        self.assertEqual(acc["sessions"]["calendar"], 6)
        self.assertEqual(acc["sessions"]["active"], 6)
        self.assertEqual(acc["sessions"]["pattern"], "calendar")
        self.assertEqual(acc["ratio"], {"value": plan.R_DEFAULT, "confidence": "low", "samples": 0, "method": "default"})
        self.assertAlmostEqual(acc["budget"]["week_per_session"], 40 / 6, places=2)
        self.assertAlmostEqual(acc["budget"]["session_per_session"], 40 / 6 / plan.R_DEFAULT, places=1)
        self.assertEqual(acc["projection"]["method"], "linear")
        self.assertEqual(acc["confidence"], "low")

    def test_partial_last_window_counts_and_idle_session(self):
        acc = plan.plan_account(item(s_used=0, s_reset=None, w_used=10, w_reset=NOW + 11 * H), [], NOW, UTC)
        self.assertFalse(acc["current"]["running"])
        self.assertEqual(acc["sessions"]["calendar"], 3)  # 5h + 5h + 1h

    def test_unplannable_items_say_why(self):
        self.assertEqual(plan.plan_account(item(long=None), [], NOW, UTC)["reason"], "no-window-pair")
        self.assertEqual(plan.plan_account(item(w_reset=NOW - 1), [], NOW, UTC)["reason"], "week-reset-passed")
        gone = dict(item(), unavailable="token-expired")
        self.assertEqual(plan.plan_account(gone, [], NOW, UTC)["reason"], "token-expired")

    def test_window_labels_generalise_to_codex_and_5h_weekly(self):
        for short, long in (("Session", "Weekly"), ("5h", "Weekly")):
            acc = plan.plan_account(item(short=short, long=long, s_reset=NOW + H, w_reset=NOW + 50 * H), [], NOW, UTC)
            self.assertTrue(acc["plannable"], (short, long))
        extra = item(s_reset=NOW + H, w_reset=NOW + 50 * H)
        extra["windows"].append({"label": "Opus week", "used": 5.0, "reset": NOW + 50 * H})
        acc = plan.plan_account(extra, [], NOW, UTC)
        self.assertEqual([w["label"] for w in acc["windows"]["extra"]], ["Opus week"])


class LearnedPaceTests(unittest.TestCase):
    def test_user_example_six_sessions_and_one_sentence(self):
        # Week 40 % left, session 20 % used, r learned as 0.12 -> 6.7 % of the week ~= 55 % of a session.
        rows, *_ = synth_history("anthropic", r=0.12, start=NOW - 6 * 86400, end=NOW - 6 * H,
                                 week_reset=NOW + 28 * H, hours={8, 14}, fill=80)
        acc = plan.plan_account(item(s_used=20, s_reset=NOW + 3 * H, w_used=60, w_reset=NOW + 28 * H),
                                rows, NOW, UTC, active_hours=[True] * 24)
        self.assertAlmostEqual(acc["ratio"]["value"], 0.12, delta=0.015)
        self.assertEqual(acc["sessions"]["active"], 6)
        self.assertAlmostEqual(acc["budget"]["week_per_session"], 6.67, delta=0.01)
        self.assertAlmostEqual(acc["budget"]["session_per_session"], 6.67 / acc["ratio"]["value"], delta=0.2)
        self.assertEqual(acc["current"]["state"], "keep_going")
        sentence = plan.summary_sentence(acc, UTC)
        self.assertTrue(sentence.startswith("6 sessions left until "), sentence)
        self.assertIn("~6.67% of the week per session", sentence)
        self.assertIn("Current session: 20% used - keep going", sentence)

    def test_steady_pace_recovers_ratio_fill_and_hours(self):
        rows, s_used, s_reset, w_used, w_reset = synth_history(
            "anthropic", r=0.15, start=NOW - 9 * 86400, end=NOW, week_reset=NOW + 3 * 86400,
            hours={9, 15}, fill=30)
        acc = plan.plan_account(item(s_used=s_used, s_reset=s_reset, w_used=w_used, w_reset=w_reset),
                                rows, NOW, UTC)
        # 30-point sessions move a whole-percent week by 4 or 5: +-1/30 quantisation.
        self.assertAlmostEqual(acc["ratio"]["value"], 0.15, delta=1 / 30 + 1e-6)
        self.assertIn(acc["ratio"]["confidence"], ("high", "medium"))
        self.assertAlmostEqual(acc["typical_fill"]["value"], 30, delta=1)
        active = set(acc["pattern"]["active_hours"])
        self.assertTrue({9, 10, 11, 15, 16, 17} <= active, active)
        self.assertFalse(active & {0, 1, 2, 3, 4, 5, 6, 21, 22, 23}, active)
        self.assertEqual(acc["sessions"]["pattern"], "history")
        # 2 sessions/day at 30 % * 0.15 = 9 %/day: 36 % after 4 days, ~63 % at reset.
        self.assertLess(acc["sessions"]["active"], acc["sessions"]["calendar"])
        self.assertEqual(acc["projection"]["method"], "typical_session")
        self.assertFalse(acc["projection"]["exhausts_before_reset"])
        self.assertIn(acc["pace"]["verdict"], ("under", "on_track"))

    def test_bursty_pace_runs_out_before_reset(self):
        rows, s_used, s_reset, w_used, w_reset = synth_history(
            "anthropic", r=0.08, start=NOW - 5 * 86400, end=NOW, week_reset=NOW + 5 * 86400,
            hours={0, 12}, fill=100)
        acc = plan.plan_account(item(s_used=s_used, s_reset=s_reset, w_used=w_used, w_reset=w_reset),
                                rows, NOW, UTC)
        self.assertEqual(acc["pace"]["verdict"], "over")
        self.assertTrue(acc["projection"]["exhausts_before_reset"])
        self.assertGreater(acc["projection"]["exhaust_at"], NOW)
        self.assertLess(acc["projection"]["exhaust_at"], w_reset)
        self.assertLess(acc["pace"]["slow_to_session_pct"], 100)
        self.assertEqual(acc["chart"]["projected"][-1][1], 100.0)

    def test_week_reset_inside_the_history(self):
        reset = NOW + 2 * 86400
        rows, s_used, s_reset, w_used, w_reset = synth_history(
            "anthropic", r=0.1, start=NOW - 8 * 86400, end=NOW, week_reset=reset, hours={10}, fill=60,
            week_used0=30)
        self.assertTrue(any(r[2] == "Current week" and r[3] == 0.0 for r in rows), "fixture must cross a reset")
        acc = plan.plan_account(item(s_used=s_used, s_reset=s_reset, w_used=w_used, w_reset=w_reset),
                                rows, NOW, UTC)
        self.assertAlmostEqual(acc["ratio"]["value"], 0.1, delta=0.015)
        start = acc["chart"]["start"]
        self.assertTrue(all(ts >= start - 60 for ts, _ in acc["chart"]["actual"]))
        self.assertEqual(acc["chart"]["ideal"], [[round(w_reset - plan.WEEK_S), 0.0], [round(w_reset), 100.0]])

    def test_active_hours_setting_overrides_the_pattern(self):
        self.assertEqual(plan.parse_active_hours("22-2"), [h in (22, 23, 0, 1) for h in range(24)])
        self.assertEqual(plan.parse_active_hours(["9:00-12:00", "14-15"]),
                         [h in (9, 10, 11, 14) for h in range(24)])
        self.assertIsNone(plan.parse_active_hours("lunch"))
        acc = plan.plan_account(item(s_used=0, s_reset=None, w_used=0, w_reset=NOW + 48 * H), [], NOW, UTC,
                                active_hours=plan.parse_active_hours("9-17"))
        self.assertEqual(acc["sessions"]["pattern"], "setting")
        self.assertEqual(acc["sessions"]["active"], 4)  # 9:00 and 14:00 starts on each of two days


class MultiAccountTests(unittest.TestCase):
    def test_two_accounts_pool_order_combined_headroom_and_reorder(self):
        rows_a, *_ = synth_history("anthropic", r=0.12, start=NOW - 4 * 86400, end=NOW - 6 * H,
                                   week_reset=NOW + 40 * H, hours={8, 14, 20}, fill=100)
        rows_b, *_ = synth_history("anthropic:pool-b", r=0.12, start=NOW - 4 * 86400, end=NOW - 6 * H,
                                   week_reset=NOW + 60 * H, hours={10}, fill=30)
        items = [
            item("anthropic:pool-b", s_used=0, s_reset=None, w_used=20, w_reset=NOW + 60 * H,
                 pool={"priority": 2, "size": 2, "strategy": "fill_first"}),
            item("anthropic", s_used=60, s_reset=NOW + 2 * H, w_used=90, w_reset=NOW + 40 * H,
                 pool={"priority": 1, "size": 2, "strategy": "fill_first"}),
        ]
        out = plan.build_plan(items, rows_a + rows_b, NOW, UTC)
        prov = out["providers"][0]
        self.assertEqual([a["provider"] for a in prov["accounts"]], ["anthropic", "anthropic:pool-b"])
        multi = prov["multi"]
        self.assertEqual(multi["use_index"], 0)
        self.assertEqual(multi["headroom_index"], 1)
        self.assertEqual(multi["combined_week_left"], 90.0)
        self.assertIn(multi["advice"], ("reorder", "switch_now"))
        self.assertEqual(multi["switch"]["next_index"], 1)

    def test_single_account_has_no_multi_block_and_unconfigured_are_skipped(self):
        items = [item(s_reset=NOW + H, w_reset=NOW + 30 * H),
                 {"provider": "zai", "base": "zai", "windows": [], "unavailable": "no-credentials"}]
        out = plan.build_plan(items, [], NOW, UTC)
        self.assertEqual(len(out["providers"]), 1)
        self.assertIsNone(out["providers"][0]["multi"])


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)
        self.qm = load("quota_monitor")

    def test_degraded_without_enrollment_reads_only_the_cache(self):
        self.home.quota_cache({"anthropic": anthropic_record(20, 60, NOW + 3 * H, NOW + 28 * H)}, iso(NOW))
        out = self.qm.plan_payload(now=NOW)
        self.assertFalse(out["enrolled"])
        self.assertTrue(out["degraded"])
        self.assertEqual(out["history_readings"], 0)
        acc = out["providers"][0]["accounts"][0]
        self.assertEqual(acc["sessions"]["calendar"], 6)
        self.assertTrue(acc["summary"].startswith("6 sessions left"))
        self.assertFalse((self.home.root / "quota_monitor.db").exists())

    def test_enrolled_plan_learns_from_the_monitor_store(self):
        self.home.enroll()
        rows, s_used, s_reset, w_used, w_reset = synth_history(
            "anthropic", r=0.2, start=NOW - 5 * 86400, end=NOW, week_reset=NOW + 2 * 86400, hours={9}, fill=40)
        db = sqlite3.connect(str(self.home.root / "quota_monitor.db"))
        db.execute("CREATE TABLE quota_history (ts REAL, provider TEXT, window TEXT, used REAL, reset REAL)")
        db.executemany("INSERT INTO quota_history VALUES (?,?,?,?,?)", rows)
        db.commit()
        db.close()
        self.home.quota_cache({"anthropic": anthropic_record(s_used, w_used, s_reset or NOW - 1, w_reset)}, iso(NOW))
        out = self.qm.plan_payload(now=NOW)
        self.assertTrue(out["enrolled"])
        self.assertFalse(out["degraded"])
        self.assertGreater(out["history_readings"], 100)
        acc = out["providers"][0]["accounts"][0]
        self.assertAlmostEqual(acc["ratio"]["value"], 0.2, delta=0.02)
        json.dumps(out)  # the CLI prints it

    def test_cli_parser_has_plan(self):
        import argparse
        cmds = load("commands")
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="quota_command")
        cmds._setup_monitor_argparse(subs)
        args = parser.parse_args(["monitor", "plan", "--json"])
        self.assertEqual(args.monitor_command, "plan")


if __name__ == "__main__":
    unittest.main()
