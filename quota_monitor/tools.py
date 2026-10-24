"""Hermes agent tools (read-only) mirroring Agent Monitor's MCP server.

Answers carry numbers and status codes only: no session titles and no upstream error text reach the calling
agent, so other sessions' content cannot steer it.

Trial contract: the tools do not register, and every handler refuses direct invocation before reading any
monitor data (a tool registry from an older session cannot be used to bypass the backend boundary).

One exception, separately opt-in: ``get_quota_plan`` registers only when
``plugins.entries.quota.settings.planToolEnabled`` is the literal YAML boolean ``true`` (default off) and
re-checks that setting on every call. It returns numbers and enum codes only (accounts are ``1..N`` in pool
order; no labels, titles, ids, paths or provider error text), so an agent can pace itself.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import trial

TOOLSET = "quota_monitor"


def _schema(name: str, description: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "additionalProperties": False}}


def _str(description: str) -> Dict[str, Any]:
    return {"type": "string", "description": description}


def _dump(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


def _blocked() -> Optional[Dict[str, Any]]:
    """Why a tool call must not proceed, or None when the trial exposes this tool."""
    if not trial.AGENT_TOOLS:
        return {"error": "agent tools are unavailable in the read-only trial",
                "error_code": "trial-tools-disabled"}
    if not trial.monitor_enrolled():
        return {"error": "Agent Monitor collection is off", "error_code": "monitor-enrollment-off"}
    return None


def _safe(fn):
    def handler(args: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> str:
        blocked = _blocked()
        if blocked is not None:
            return _dump(blocked)
        try:
            return _dump(fn(args or {}))
        except Exception as e:  # never leak a traceback / upstream text: the error class is enough
            return _dump({"error": "quota monitor unavailable (%s)" % type(e).__name__})
    handler.__name__ = getattr(fn, "__name__", "handler")
    return handler


def _inputs() -> Dict[str, Any]:
    from . import _routing_inputs
    return _routing_inputs()


@_safe
def get_quota_status(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    i = _inputs()
    rows = advice.quota_rows(i["items"], i["burn"], i["health"])
    provider = args.get("provider")
    if provider:
        rows = [r for r in rows if r["provider"] == provider or advice.base_provider(r["provider"]) == provider]
    return {"providers": rows}


@_safe
def get_model_health(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    return advice.model_health(_inputs()["health"], args.get("provider"), bool(args.get("include_idle")))


@_safe
def get_fallback_chains(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    return advice.fallback_chains(_inputs()["health"], args.get("profile"))


@_safe
def get_budget(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import budget_payload
    out = budget_payload()
    out.pop("generated_at", None)
    out.pop("monitor_version", None)
    return out


def _live() -> Dict[str, Any]:
    from . import live
    return live.live(record_sample=False)


@_safe
def get_running_counts(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    return advice.running_counts(_live())


@_safe
def get_agent_attention(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    return advice.agent_attention(_live())


@_safe
def recommend_model(args: Dict[str, Any]) -> Dict[str, Any]:
    from . import advice
    i = _inputs()
    exclude = args.get("exclude_providers")
    exclude_list: List[str] = [str(x) for x in exclude] if isinstance(exclude, list) else []
    try:
        min_left = int(args.get("min_quota_left_pct", 10))
    except (TypeError, ValueError):
        min_left = 10
    try:
        top = int(args.get("top", 3))
    except (TypeError, ValueError):
        top = 3
    return advice.recommend_model(i["items"], i["burn"], i["health"], args.get("prefer_provider"), exclude_list,
                                  min_left, top)


TOOLS = (
    (get_quota_status, _schema(
        "get_quota_status",
        "Remaining quota per provider from the quota plugin's cache: % used/left per window, reset time, burn rate, "
        "projected time the window fills, and whether the provider has a usable account. Read-only, local.",
        {"provider": _str("Filter to one provider (e.g. anthropic, openai-codex, copilot).")}), "📊"),
    (get_model_health, _schema(
        "get_model_health",
        "Per-model health over the last 24 h: status (ok, degraded, failing, cooldown, account_exhausted, idle), "
        "calls, error rate, error codes, cooldowns and turn speed. Upstream error text is omitted.",
        {"provider": _str("Filter to one provider."),
         "include_idle": {"type": "boolean", "description": "Also list idle models."}}), "🩺"),
    (get_fallback_chains, _schema(
        "get_fallback_chains",
        "For each Hermes profile: primary model and each fallback with its state (ok, risky, skip) and reason code "
        "(same_backend, no_credentials, locked, cooldown, error_rate).",
        {"profile": _str("Filter to one profile.")}), "🔗"),
    (get_budget, _schema(
        "get_budget",
        "Estimated spend (Hermes + agent CLIs, API-price equivalent): today, yesterday, month to date and the "
        "estimated API spend, against the daily / monthly budget set in the Quota widget. "
        "No month-end projection (not a billed amount).", {}), "💰"),
    (get_running_counts, _schema(
        "get_running_counts",
        "How many agent sessions and subagents run right now per source and profile, with main-session states "
        "(working, needs_approval, waiting, stalled), largest context and possible tool loops. Counts only.", {}), "🏃"),
    (get_agent_attention, _schema(
        "get_agent_attention",
        "Running agent sessions that need the user: approval, finished (your turn), stalled, repeated tool calls, "
        "very large context. Most urgent first; no titles.", {}), "🔔"),
    (recommend_model, _schema(
        "recommend_model",
        "Rank the models work can be routed to right now: healthy, usable account, quota headroom (not projected "
        "to fill before reset), proven recent use, faster median turn. Call before delegating or picking a model.",
        {"prefer_provider": _str("Provider to favour."),
         "exclude_providers": {"type": "array", "items": {"type": "string"}, "description": "Providers to skip."},
         "min_quota_left_pct": {"type": "integer", "description": "Minimum % left in every window (default 10)."},
         "top": {"type": "integer", "description": "How many candidates to return (default 3)."}}), "🧭"),
)


# ---------- opt-in pacing tool (planToolEnabled) ----------

PLAN_TOOL_SETTING = "planToolEnabled"
_CODE_RE = None


def plan_tool_enabled() -> bool:
    """Only the literal YAML boolean ``true`` in the active profile's config enables the plan tool."""
    from .common import hermes_home, read_config
    node: Any = read_config(hermes_home())
    for part in ("plugins", "entries", "quota", "settings", PLAN_TOOL_SETTING):
        node = node.get(part) if isinstance(node, dict) else None
    return node is True


def _code(value: Any, fallback: str = "unavailable") -> Optional[str]:
    """A machine code (``token-expired``, ``http-429``...) or ``fallback``; never free text."""
    global _CODE_RE
    import re
    if value is None:
        return None
    _CODE_RE = _CODE_RE or re.compile(r"^[a-z0-9][a-z0-9_:-]{0,39}$")
    return value if isinstance(value, str) and _CODE_RE.match(value) else fallback


def _num(value: Any) -> Optional[float]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def plan_for_agent(payload: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Reduce a ``plan_payload()`` to numbers and enums; accounts become 1-based pool positions."""
    import time
    now = time.time() if now is None else now

    def rel(ts: Any) -> Optional[int]:
        return int(round(ts - now)) if _num(ts) is not None else None

    providers = []
    for prov in payload.get("providers") or []:
        accounts = []
        for n, acc in enumerate(prov.get("accounts") or []):
            row: Dict[str, Any] = {"account": n + 1, "plannable": bool(acc.get("plannable"))}
            pool = acc.get("pool") if isinstance(acc.get("pool"), dict) else {}
            if _num(pool.get("priority")) is not None:
                row["pool_position"] = int(pool["priority"])
            if not acc.get("plannable"):
                row["reason"] = _code(acc.get("reason"))
                accounts.append(row)
                continue
            win, cur, proj = acc["windows"], acc["current"], acc["projection"]
            row.update({
                "session_used_pct": _num(win["short"]["used"]), "session_running": bool(cur["running"]),
                "seconds_to_session_reset": rel(cur.get("reset")),
                "week_used_pct": _num(win["long"]["used"]), "week_left_pct": _num(acc["week_left"]),
                "seconds_to_week_reset": rel(win["long"]["reset"]),
                "sessions_left_calendar": acc["sessions"]["calendar"], "sessions_left_active": acc["sessions"]["active"],
                "sessions_source": _code(acc["sessions"]["pattern"]),
                "week_pct_per_session_pct": _num(acc["ratio"]["value"]),
                "ratio_confidence": _code(acc["ratio"]["confidence"]),
                "budget_week_pct_per_session": _num(acc["budget"]["week_per_session"]),
                "budget_session_pct_per_session": _num(acc["budget"]["session_per_session"]),
                "current_session_target_pct": _num(cur["target_used"]), "current_state": _code(cur["state"]),
                "current_can_fill": bool(cur["can_fill"]),
                "projected_week_at_reset_pct": _num(proj["week_at_reset"]),
                "projection_method": _code(proj["method"]),
                "exhausts_before_reset": bool(proj["exhausts_before_reset"]),
                "seconds_to_exhaust": rel(proj.get("exhaust_at")),
                "pace": _code(acc["pace"]["verdict"]),
                "extra_session_pct": _num(acc["pace"]["extra_session_pct"]),
                "slow_to_session_pct": _num(acc["pace"]["slow_to_session_pct"]),
                "confidence": _code(acc["confidence"]),
            })
            accounts.append(row)
        multi = prov.get("multi")
        out_multi = None
        if isinstance(multi, dict):
            def pos(i: Any) -> Optional[int]:
                return int(i) + 1 if _num(i) is not None else None
            out_multi = {"strategy": _code(multi.get("strategy")), "use_account": pos(multi.get("use_index")),
                         "headroom_account": pos(multi.get("headroom_index")), "advice": _code(multi.get("advice")),
                         "combined_week_left_pct": _num(multi.get("combined_week_left")),
                         "combined_week_pct_per_session": _num(multi.get("combined_week_per_session"))}
        providers.append({"provider": _code(prov.get("base"), "provider"), "accounts": accounts, "multi": out_multi})
    return {"plan_version": payload.get("plan_version"), "degraded": bool(payload.get("degraded")),
            "history_readings": _num(payload.get("history_readings")), "providers": providers}


def get_quota_plan(args: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> str:
    try:
        if not plan_tool_enabled():
            return _dump({"error": "the quota plan tool is off", "error_code": "plan-tool-disabled"})
        from . import plan_payload
        out = plan_for_agent(plan_payload())
        provider = (args or {}).get("provider")
        if isinstance(provider, str) and provider:
            out["providers"] = [p for p in out["providers"] if p["provider"] == provider]
        return _dump(out)
    except Exception as e:  # never leak a traceback / upstream text: the error class is enough
        return _dump({"error": "quota plan unavailable (%s)" % type(e).__name__})


PLAN_TOOL = (get_quota_plan, _schema(
    "get_quota_plan",
    "Quota pacing for subscription accounts with a 5-hour session and a weekly window (Anthropic, Codex): "
    "sessions left before the weekly reset, weekly %% budget per session (and as session %%), whether the "
    "current session can be used to 100%%, projected weekly %% at reset, pace verdict (under, on_track, over) "
    "and, for several pooled accounts, which one (1..N in pool order) to use now. Numbers and codes only; "
    "read-only, local, no network. Call before long or expensive work to decide whether to slow down or pick a "
    "cheaper model.".replace("%%", "%"),
    {"provider": _str("Filter to one provider (e.g. anthropic, openai-codex).")}), "🗓️")


def register_tools(ctx: Any) -> List[str]:
    """Register every tool on a Hermes plugin context; returns the names registered.

    The read-only trial registers none of the monitor tools: agents cannot gain monitor scope through the
    tool registry. ``get_quota_plan`` registers only while ``planToolEnabled`` is literally true.
    """
    done: List[str] = []
    register = getattr(ctx, "register_tool", None)
    if not callable(register):
        return done
    try:
        plan_on = plan_tool_enabled()
    except Exception:
        plan_on = False
    if plan_on:
        handler, schema, emoji = PLAN_TOOL
        try:
            register(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                     description=schema["description"], emoji=emoji)
            done.append(schema["name"])
        except Exception:
            pass
    if not trial.AGENT_TOOLS:
        return done
    for handler, schema, emoji in TOOLS:
        try:
            register(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                     description=schema["description"], emoji=emoji)
            done.append(schema["name"])
        except Exception:
            continue
    return done
