"""Quota pacing planner: session (short) window vs week (long) window, per account.

Pure: no network, no writes. Inputs are the plugin's quota cache items (current used % and reset per window)
and, when the monitor is enrolled, the monitor's own quota history (``quota_monitor.db``). Everything here is
an estimate from those readings, never a provider guarantee; every derived number carries the inputs and the
method it came from so the UI can show the formula and the confidence.

Terms (per account):

* ``s_used``/``s_reset``: short window used % and reset (Anthropic "Current session", Codex "Session", 5h).
* ``w_used``/``w_reset``: long window used % and reset (Anthropic "Current week", Codex "Weekly", 7d).
* ``r``: weekly % consumed per 1 % of session. Learned as the Δsession-weighted median, over past session
  windows, of ``Δweek / Δsession`` between the first and last reading of that session window (same week window,
  no reset in between, ``Δsession >= R_MIN_DS``). Providers report the week as a whole percent, so a short
  session's ratio is dominated by rounding; weighting by Δsession lets long sessions decide. Without enough
  history: ``R_DEFAULT`` with confidence ``low``.
* ``typical_fill``: median of the highest session % reached in each finished session window.
* active pattern: share of observed days on which the session % rose during each local hour of day.
* sessions left: ``calendar`` = the current session (if one is running) plus every 5h window that can still
  start before the week resets (a final partial window counts); ``active`` = the windows the user's pattern
  (or ``planActiveHours``) would actually start, simulated hour by hour.
* budget per session = ``week_left / sessions_left`` weekly %, i.e. ``/ r`` session %.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PLAN_VERSION = 1
SESSION_S = 5 * 3600
WEEK_S = 7 * 86400
R_DEFAULT = 0.10            # a full session ~= 10 % of the week when nothing was learned yet
R_MIN_DS = 10.0             # a session sample needs >= 10 session points (weekly % is integer-quantised)
R_MIN_SAMPLES_HIGH = 3
RESET_TOLERANCE_S = 15 * 60  # providers jitter reset stamps by fractions of a second to minutes
DROP_EPS = 0.5
ACTIVE_P = 0.5              # an hour of day counts as "active" when used on >= half of the observed days
MIN_PATTERN_DAYS = 2
UNDER_RATIO = 0.75
OVER_RATIO = 1.0
LINEAR_OVER_PTS = 5.0
LINEAR_UNDER_PTS = 10.0
CHART_MAX_POINTS = 96
HISTORY_DAYS = 14

SHORT_LABELS = {"current session": SESSION_S, "session": SESSION_S, "5h": SESSION_S,
                "5-hour": SESSION_S, "five hour": SESSION_S}
LONG_LABELS = {"current week": WEEK_S, "weekly": WEEK_S, "week": WEEK_S, "7d": WEEK_S}


# ---------- small helpers ----------

def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _r1(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def classify(label: Any) -> Optional[str]:
    """``short`` / ``long`` / ``extra`` (model-scoped long window) / None."""
    text = str(label or "").strip().lower()
    if text in SHORT_LABELS:
        return "short"
    if text in LONG_LABELS:
        return "long"
    if text.endswith(" week") or text.endswith(" weekly"):
        return "extra"
    return None


def pair_windows(windows: Sequence[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]],
                                                              List[Dict[str, Any]]]:
    short = long = None
    extras: List[Dict[str, Any]] = []
    for w in windows or []:
        if not isinstance(w, dict):
            continue
        kind = classify(w.get("label"))
        if kind == "short" and short is None:
            short = w
        elif kind == "long" and long is None:
            long = w
        elif kind == "extra":
            extras.append(w)
    return short, long, extras


def parse_active_hours(value: Any) -> Optional[List[bool]]:
    """``"09-23"``, ``"9:00-1:00"`` (crosses midnight) or a list of such ranges -> 24 local hour flags."""
    ranges = value if isinstance(value, list) else [value]
    hours = [False] * 24
    seen = False
    for item in ranges:
        if not isinstance(item, str):
            continue
        m = re.fullmatch(r"\s*(\d{1,2})(?::00)?\s*-\s*(\d{1,2})(?::00)?\s*", item)
        if not m:
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if not (0 <= start <= 24 and 0 <= end <= 24):
            continue
        seen = True
        h = start % 24
        span = (end - start) % 24 or 24
        for _ in range(span):
            hours[h] = True
            h = (h + 1) % 24
    return hours if seen and any(hours) else None


# ---------- history ----------

def _series(rows: Iterable[Sequence[Any]], provider: str, short_label: str, long_label: str
            ) -> List[Tuple[float, Optional[float], Optional[float], Optional[float], Optional[float]]]:
    """Paired readings ``(ts, s_used, s_reset, w_used, w_reset)`` for one account, by timestamp."""
    by_ts: Dict[float, Dict[str, Tuple[Optional[float], Optional[float]]]] = defaultdict(dict)
    for ts, prov, win, used, reset in rows:
        if prov != provider or win not in (short_label, long_label):
            continue
        t = _finite(ts)
        if t is None:
            continue
        by_ts[round(t, 3)]["s" if win == short_label else "w"] = (_finite(used), _finite(reset))
    out = []
    for t in sorted(by_ts):
        s = by_ts[t].get("s", (None, None))
        w = by_ts[t].get("w", (None, None))
        out.append((t, s[0], s[1], w[0], w[1]))
    return out


def _same_reset(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) <= RESET_TOLERANCE_S


def session_segments(series) -> List[List[tuple]]:
    """Split readings into session windows: a new window starts when the session reset moves or used drops."""
    segments: List[List[tuple]] = []
    current: List[tuple] = []
    for p in series:
        _t, s_used, s_reset, _w, _wr = p
        if s_used is None:
            continue
        if s_reset is None and s_used <= 0:
            if current:
                segments.append(current)
                current = []
            continue  # idle: no session window running
        if current:
            last = current[-1]
            moved = s_reset is not None and last[2] is not None and not _same_reset(s_reset, last[2])
            dropped = s_used < last[1] - DROP_EPS
            if moved or dropped:
                segments.append(current)
                current = []
        current.append(p)
    if current:
        segments.append(current)
    return segments


def ratio_samples(segments) -> List[Tuple[float, float]]:
    """``(Δweek/Δsession, Δsession)`` per session window, inside one week window and without a week drop."""
    samples = []
    for seg in segments:
        runs: List[List[tuple]] = []
        run: List[tuple] = []
        for p in seg:
            if p[3] is None:
                continue
            if run:
                prev = run[-1]
                if (p[3] < prev[3] - DROP_EPS) or (p[4] is not None and prev[4] is not None
                                                   and not _same_reset(p[4], prev[4])):
                    runs.append(run)
                    run = []
            run.append(p)
        if run:
            runs.append(run)
        for run in runs:
            ds = run[-1][1] - run[0][1]
            dw = run[-1][3] - run[0][3]
            if ds >= R_MIN_DS and dw >= 0:
                samples.append((dw / ds, ds))
    return samples


def weighted_median(pairs: Sequence[Tuple[float, float]]) -> float:
    """Median of values weighted by their weights (lower median on an exact tie)."""
    ordered = sorted(pairs)
    total = sum(w for _v, w in ordered)
    acc = 0.0
    for value, weight in ordered:
        acc += weight
        if acc >= total / 2.0:
            return value
    return ordered[-1][0]


def estimate_r(samples: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    if not samples:
        return {"value": R_DEFAULT, "confidence": "low", "samples": 0, "method": "default"}
    value = weighted_median(samples)
    if value <= 0:
        return {"value": R_DEFAULT, "confidence": "low", "samples": len(samples), "method": "default"}
    confidence = "medium"
    if len(samples) >= R_MIN_SAMPLES_HIGH:
        q = sorted(v for v, _w in samples)
        lo, hi = q[len(q) // 4], q[(3 * len(q)) // 4]
        if (hi - lo) / value <= 0.6:
            confidence = "high"
    return {"value": value, "confidence": confidence, "samples": len(samples), "method": "weighted_median"}


def typical_fill(segments, now: float) -> Optional[Dict[str, Any]]:
    """Median of the highest session % reached in each finished session window."""
    fills = []
    for seg in segments:
        reset = seg[-1][2]
        finished = (reset is not None and reset <= now) or (reset is None and seg[-1][0] < now - SESSION_S)
        peak = max(p[1] for p in seg)
        if finished and peak > 0:
            fills.append(peak)
    if not fills:
        return None
    return {"value": statistics.median(fills), "samples": len(fills)}


def activity_pattern(series, tz: tzinfo) -> Optional[Dict[str, Any]]:
    """Per local hour of day: share of observed days on which the session % rose during that hour."""
    observed: Dict[int, set] = defaultdict(set)
    active: Dict[int, set] = defaultdict(set)
    days = set()
    prev = None
    for p in series:
        if p[1] is None:
            continue
        local = datetime.fromtimestamp(p[0], tz)
        day = local.date()
        days.add(day)
        observed[local.hour].add(day)
        if prev is not None and p[0] - prev[0] <= 2 * 3600:
            rose = p[1] > prev[1] + 1e-9 and (prev[2] is None or p[2] is None or _same_reset(p[2], prev[2]))
            if rose:
                # Attribute the rise to every hour between the two readings.
                t = prev[0]
                while t <= p[0]:
                    lt = datetime.fromtimestamp(t, tz)
                    active[lt.hour].add(lt.date())
                    t += 3600
                active[local.hour].add(day)
        prev = p
    if len(days) < MIN_PATTERN_DAYS:
        return None
    probs = []
    for h in range(24):
        seen = len(observed[h] | active[h])
        probs.append(round(len(active[h]) / seen, 3) if seen else 0.0)
    return {"hours": probs, "days": len(days), "active_hours": [h for h in range(24) if probs[h] >= ACTIVE_P]}


# ---------- sessions ahead ----------

def calendar_sessions(now: float, s_reset: Optional[float], w_reset: float, running: bool) -> int:
    start = s_reset if running and s_reset is not None else now
    ahead = max(0.0, w_reset - start)
    return (1 if running else 0) + int(math.ceil(ahead / SESSION_S - 1e-9))


def simulate_sessions(now: float, s_reset: Optional[float], w_reset: float, running: bool,
                      hours: Optional[List[bool]], tz: tzinfo) -> List[Tuple[float, float]]:
    """Windows ``[(start, end)]`` the user would start before the week resets.

    The running session is the first window. After it, walk hour by hour from its end: a window starts at the
    first active hour and occupies 5 h. ``hours=None`` means every hour is usable (calendar count).
    """
    windows: List[Tuple[float, float]] = []
    t = now
    if running and s_reset is not None:
        windows.append((max(now, s_reset - SESSION_S), s_reset))
        t = s_reset
    guard = 0
    while t < w_reset and guard < 400:
        guard += 1
        if hours is None or hours[datetime.fromtimestamp(t, tz).hour]:
            windows.append((t, min(t + SESSION_S, w_reset)))
            t += SESSION_S
        else:
            # next whole local hour
            local = datetime.fromtimestamp(t, tz)
            nxt = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
            t = max(nxt, t + 60)
    return windows


# ---------- one account ----------

def _window_values(w: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    if not w:
        return None, None
    return _finite(w.get("used")), _finite(w.get("reset"))


def _chart(series, now: float, w_used: float, w_reset: float, projected: Optional[float],
           exhaust_at: Optional[float]) -> Dict[str, Any]:
    start = w_reset - WEEK_S
    actual = [[round(p[0]), p[3]] for p in series
              if p[3] is not None and p[0] >= start - 60 and p[0] <= now + 60
              and (p[4] is None or _same_reset(p[4], w_reset))]
    # A provider-side reset inside the window (early reset, moved reset time) starts the line over.
    for i in range(len(actual) - 1, 0, -1):
        if actual[i][1] < actual[i - 1][1] - 5.0:
            actual = actual[i:]
            break
    if len(actual) > CHART_MAX_POINTS:
        step = len(actual) / float(CHART_MAX_POINTS)
        actual = [actual[int(i * step)] for i in range(CHART_MAX_POINTS - 1)] + [actual[-1]]
    if not actual or actual[-1][0] < now - 60:
        actual.append([round(now), w_used])
    out: Dict[str, Any] = {"start": round(start), "end": round(w_reset), "actual": actual,
                           "ideal": [[round(start), 0.0], [round(w_reset), 100.0]]}
    if projected is not None:
        if exhaust_at is not None and exhaust_at < w_reset:
            out["projected"] = [[round(now), w_used], [round(exhaust_at), 100.0]]
        else:
            out["projected"] = [[round(now), w_used], [round(w_reset), _r1(_clamp(projected))]]
    return out


def plan_account(item: Dict[str, Any], rows: Sequence[Sequence[Any]], now: float, tz: tzinfo,
                 active_hours: Optional[List[bool]] = None, history: bool = True) -> Dict[str, Any]:
    """The full plan for one cache item, or ``{"plannable": False, "reason": ...}``."""
    base = {"provider": item.get("provider"), "base": item.get("base"), "label": item.get("name"),
            "account_label": item.get("account_label"), "pool": item.get("pool")}
    if item.get("unavailable"):
        return dict(base, plannable=False, reason=str(item.get("unavailable")))
    short, long_w, extras = pair_windows(item.get("windows") or [])
    if not short or not long_w:
        return dict(base, plannable=False, reason="no-window-pair")
    s_used, s_reset = _window_values(short)
    w_used, w_reset = _window_values(long_w)
    if s_used is None or w_used is None or w_reset is None:
        return dict(base, plannable=False, reason="missing-values")
    if w_reset <= now:
        return dict(base, plannable=False, reason="week-reset-passed")
    s_used, w_used = _clamp(s_used), _clamp(w_used)
    running = s_reset is not None and s_reset > now
    if not running:
        s_used = 0.0  # an expired or idle session window is fresh on next use
    week_left = 100.0 - w_used

    series = _series(rows, str(item.get("provider")), str(short.get("label")), str(long_w.get("label"))) \
        if history else []
    segments = session_segments(series)
    r_info = estimate_r(ratio_samples(segments))
    r = r_info["value"]
    fill = typical_fill(segments, now)
    pattern = activity_pattern(series, tz) if series else None

    if active_hours is not None:
        hours, pattern_source = active_hours, "setting"
    elif pattern is not None:
        flags = [p >= ACTIVE_P for p in pattern["hours"]]
        hours, pattern_source = (flags, "history") if any(flags) else (None, "calendar")
    else:
        hours, pattern_source = None, "calendar"

    cal = calendar_sessions(now, s_reset, w_reset, running)
    windows = simulate_sessions(now, s_reset, w_reset, running, hours, tz)
    active = len(windows) if hours is not None else cal
    active = max(1, active)

    budget_week = week_left / active
    budget_session = budget_week / r if r > 0 else None
    target = _clamp(s_used + budget_session) if budget_session is not None else None
    fill_cost = (100.0 - s_used) * r
    current = {
        "used": _r1(s_used), "running": running, "reset": s_reset if running else None,
        "seconds_to_reset": round(s_reset - now) if running else None,
        "target_used": _r1(target),
        "can_fill": bool(target is not None and target >= 100.0 - 1e-9),
        "fill_week_cost": _r1(fill_cost),
        "fill_exhausts_week": bool(fill_cost > week_left + 1e-9),
    }
    if target is None:
        current["state"] = "unknown"
    elif s_used > target + 1e-9:
        current["state"] = "over_target"
    elif target - s_used <= 5.0 and target < 100.0:
        current["state"] = "near_target"
    else:
        current["state"] = "keep_going"

    # Projection at the user's typical pace (history) or the week's straight-line rate so far (no history).
    exhaust_at = None
    if fill is not None:
        f = fill["value"]
        method = "typical_session"
        per_window = []
        for i, (start, end) in enumerate(windows):
            spend = (max(0.0, f - s_used) if (i == 0 and running) else f) * r
            per_window.append((start, end, spend))
        projected = w_used + sum(s for _a, _b, s in per_window)
        if projected > 100.0:
            acc = w_used
            for start, end, spend in per_window:
                if spend > 0 and acc + spend >= 100.0:
                    span = max(60.0, end - max(start, now))
                    exhaust_at = max(start, now) + span * (100.0 - acc) / spend
                    break
                acc += spend
        expected = projected - w_used
        ratio = expected / week_left if week_left > 0 else math.inf
        if ratio > OVER_RATIO:
            verdict = "over"
        elif ratio < UNDER_RATIO:
            verdict = "under"
        else:
            verdict = "on_track"
        # Room per session beyond the typical fill; a session cannot exceed 100 %.
        pace_delta = (min(100.0, budget_session) - f) if budget_session is not None else None
    else:
        method = "linear"
        elapsed = max(1.0, now - (w_reset - WEEK_S))
        rate = w_used / elapsed
        projected = w_used + rate * (w_reset - now)
        if projected > 100.0 and rate > 0:
            exhaust_at = now + (100.0 - w_used) / rate
        ideal_now = _clamp(elapsed / WEEK_S * 100.0)
        gap = w_used - ideal_now
        verdict = "over" if gap > LINEAR_OVER_PTS else "under" if gap < -LINEAR_UNDER_PTS else "on_track"
        pace_delta = None
        ratio = None

    out = dict(base)
    out.update({
        "plannable": True,
        "windows": {"short": {"label": short.get("label"), "used": _r1(s_used), "reset": s_reset},
                    "long": {"label": long_w.get("label"), "used": _r1(w_used), "reset": w_reset},
                    "extra": [{"label": w.get("label"), "used": _r1(_finite(w.get("used"))),
                               "reset": _finite(w.get("reset"))} for w in extras]},
        "week_left": _r1(week_left),
        "seconds_to_week_reset": round(w_reset - now),
        "sessions": {"calendar": cal, "active": active, "pattern": pattern_source,
                     "next_starts": [round(a) for a, _b in windows[:12]]},
        "ratio": {"value": round(r, 4), "confidence": r_info["confidence"], "samples": r_info["samples"],
                  "method": r_info["method"]},
        "typical_fill": None if fill is None else {"value": _r1(fill["value"]), "samples": fill["samples"]},
        "pattern": pattern,
        "budget": {"week_per_session": _r1(budget_week, 2), "session_per_session": _r1(budget_session)},
        "current": current,
        "projection": {"method": method, "week_at_reset": _r1(_clamp(projected, 0.0, 999.0)),
                       "exhausts_before_reset": exhaust_at is not None and exhaust_at < w_reset,
                       "exhaust_at": round(exhaust_at) if exhaust_at is not None and exhaust_at < w_reset else None,
                       "use_ratio": None if ratio is None or not math.isfinite(ratio) else round(ratio, 3)},
        "pace": {"verdict": verdict,
                 "extra_session_pct": _r1(pace_delta) if verdict == "under" and pace_delta is not None else None,
                 "slow_to_session_pct": _r1(max(0.0, budget_session))
                 if verdict == "over" and budget_session is not None else None},
        "chart": _chart(series, now, w_used, w_reset, projected, exhaust_at),
        "confidence": _confidence(r_info["confidence"], fill, pattern_source),
    })
    return out


def _confidence(r_conf: str, fill: Optional[Dict[str, Any]], pattern_source: str) -> str:
    if r_conf == "low" or fill is None:
        return "low"
    if r_conf == "high" and pattern_source in ("history", "setting") and fill["samples"] >= 3:
        return "high"
    return "medium"


# ---------- all accounts ----------

def _order_key(index_item: Tuple[int, Dict[str, Any]]) -> Tuple[int, float, int]:
    index, item = index_item
    pool = item.get("pool") if isinstance(item.get("pool"), dict) else None
    pri = _finite(pool.get("priority")) if pool else None
    return (0 if pri is not None else 1, pri if pri is not None else 0.0, index)


def _usable(acc: Dict[str, Any]) -> bool:
    return bool(acc.get("plannable")) and (acc.get("week_left") or 0) > 1.0 \
        and (100.0 - (acc["current"]["used"] or 0)) > 1.0


def multi_account(accounts: List[Dict[str, Any]], strategy: Optional[str]) -> Optional[Dict[str, Any]]:
    """Which account to use now and when to switch, for one provider's plannable accounts (pool order)."""
    plannable = [a for a in accounts if a.get("plannable")]
    if len(plannable) < 2:
        return None
    strategy = strategy or "fill_first"
    combined_left = sum(a["week_left"] for a in plannable)
    combined_active = max(a["sessions"]["active"] for a in plannable)
    first = next((i for i, a in enumerate(accounts) if _usable(a)), None)
    headroom = max(range(len(accounts)), key=lambda i: (
        (accounts[i]["budget"]["week_per_session"] or 0) if _usable(accounts[i]) else -1, -i))
    advice = "use_first"
    switch = None
    if first is None:
        advice = "all_exhausted"
    else:
        cur = accounts[first]
        if cur["pace"]["verdict"] == "over" and headroom != first and _usable(accounts[headroom]):
            advice = "reorder"
        if cur["current"]["state"] in ("near_target", "over_target") and headroom != first:
            advice = "switch_now"
        switch = {"at_session_used": cur["current"]["target_used"], "reset": cur["current"]["reset"],
                  "next_index": next((i for i, a in enumerate(accounts) if i != first and _usable(a)), None)}
    return {
        "strategy": strategy,
        "use_index": first,
        "headroom_index": headroom if _usable(accounts[headroom]) else None,
        "advice": advice if strategy == "fill_first" else ("spread" if first is not None else "all_exhausted"),
        "switch": switch,
        "combined_week_left": _r1(combined_left),
        "combined_week_per_session": _r1(combined_left / max(1, combined_active), 2),
        "accounts": len(plannable),
    }


def build_plan(items: List[Dict[str, Any]], rows: Sequence[Sequence[Any]], now: float, tz: tzinfo,
               active_hours: Optional[List[bool]] = None, history: bool = True,
               strategies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Plans for every item that exposes a short+long window pair, grouped by base provider."""
    groups: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        short, long_w, _extras = pair_windows(item.get("windows") or [])
        if not (short and long_w) and not item.get("unavailable"):
            continue
        if item.get("unavailable") and not (item.get("pool") or ":" in str(item.get("provider"))):
            continue  # plain unconfigured providers are not accounts worth planning
        groups[str(item.get("base") or item.get("provider"))].append((index, item))
    providers = []
    for base_id, members in groups.items():
        members.sort(key=_order_key)
        accounts = [plan_account(item, rows, now, tz, active_hours, history) for _i, item in members]
        if not any(a.get("plannable") for a in accounts):
            continue
        strategy = None
        for a in accounts:
            if isinstance(a.get("pool"), dict) and a["pool"].get("strategy"):
                strategy = a["pool"]["strategy"]
                break
        if strategy is None and strategies:
            strategy = strategies.get(base_id)
        for n, a in enumerate(accounts):
            a["index"] = n
        providers.append({"base": base_id, "accounts": accounts, "multi": multi_account(accounts, strategy)})
    return {"plan_version": PLAN_VERSION, "now": now, "providers": providers}


def summary_sentence(acc: Dict[str, Any], tz: tzinfo) -> str:
    """One English sentence for the CLI and agents (the widget builds its own, localised)."""
    if not acc.get("plannable"):
        return "No plan: %s." % acc.get("reason")
    reset = datetime.fromtimestamp(acc["windows"]["long"]["reset"], tz).strftime("%a %H:%M")
    s = acc["sessions"]["active"]
    cur = acc["current"]
    state = {"keep_going": "keep going", "near_target": "close to this session's share",
             "over_target": "over this session's share; slow down or switch account",
             "unknown": "no estimate"}[cur["state"]]
    session_pct = acc["budget"]["session_per_session"]
    return ("%d session%s left until %s; you can use ~%s%% of the week per session (~%s%% of a session). "
            "Current session: %s%% used - %s." % (
                s, "" if s == 1 else "s", reset, acc["budget"]["week_per_session"],
                "?" if session_pct is None else ("%.0f" % session_pct), "%.0f" % (cur["used"] or 0), state))


def local_tz(name: Any) -> tzinfo:
    if isinstance(name, str) and name.strip():
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name.strip())
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc
