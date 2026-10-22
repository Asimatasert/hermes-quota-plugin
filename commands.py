"""Slash + CLI command handlers for the quota plugin.

Mirrors the Hermes plugin convention (see the lifeos plugin): a synchronous
``quota_command(raw_args) -> str`` for the in-session ``/quota`` slash command,
and an argparse-backed ``hermes quota`` CLI command via ``register_cli_command``.

Subcommands (slash):
    /quota              → per-provider quota status (refreshes if cache is stale)
    /quota refresh      → force a re-fetch of every provider, then show status
    /quota <provider>   → status for a single provider (e.g. /quota grok)
    /quota help         → usage

CLI:
    hermes quota              → status
    hermes quota refresh      → force re-fetch
    hermes quota provider X   → single provider
    hermes quota monitor ...  → Agent Monitor views as JSON (snapshot, history,
                                recommend, budget, set-budget, accounts,
                                set-priority, set-strategy; see docs/monitor.md)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .quota_cache import iter_account_records, read_quota_cache, quota_cache_age_seconds, refresh_quota_cache, MAX_AGE_S
from .quota_providers import PROVIDER_FETCHERS

_PROVIDER_IDS = tuple(PROVIDER_FETCHERS)

QUOTA_HELP = (
    "**/quota** — per-provider quota / rate-limit status\n"
    "\n"
    "• `/quota` — show all providers (auto-refreshes if the cache is stale)\n"
    "• `/quota refresh` — force a re-fetch of every provider\n"
    "• `/quota <provider>` — e.g. `/quota grok`, `/quota openai-codex`\n"
    "• `/quota help` — this message\n"
    "\n"
    "Also available as `hermes quota` on the CLI."
)


def _short_reset(reset_iso: Optional[str]) -> str:
    """Render an ISO reset timestamp as a short local 'reset <when>' string."""
    if not reset_iso:
        return ""
    try:
        dt = datetime.fromisoformat(reset_iso)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    now = datetime.now()
    delta = (local.date() - now.date()).days
    if delta == 0:
        day = "today"
    elif delta == 1:
        day = "tomorrow"
    else:
        day = local.strftime("%b %d")
    return f"{day} {local.strftime('%H:%M')}"


def _age_label() -> str:
    age = quota_cache_age_seconds()
    if age is None:
        return "never fetched"
    if age <= MAX_AGE_S:
        return f"fetched {int(age // 60)}m ago"
    return f"stale ({int(age // 60)}m old)"


def _matches_filter(pf: str, name: str, rec: Any) -> bool:
    """Whether a ``/quota <filter>`` matches this provider row.

    A row matches on its cache key, its label, and — for an account row — its
    base provider and its account label, so ``/quota anthropic`` lists the
    primary login and every configured Claude account together.
    """
    candidates = {name.lower()}
    if isinstance(rec, dict):
        for key in ("label", "provider", "account_label"):
            value = rec.get(key)
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip().lower())
    return pf in candidates


def _render_quota(provider_filter: Optional[str]) -> str:
    """Render the per-provider quota breakdown (optionally filtered)."""
    cache = read_quota_cache()
    providers = cache.get("providers") or {}
    if not providers:
        return "📊 quota: no providers configured / no data fetched yet.\n" \
               "Run `/quota refresh` (or `hermes quota refresh`) to populate it."

    pf = (provider_filter or "").strip().lower()
    lines = [f"📊 **quota** ({_age_label()})", ""]
    shown = 0
    for name, rec in iter_account_records(providers):
        if pf and not _matches_filter(pf, name, rec):
            continue
        shown += 1
        if not isinstance(rec, dict):
            continue
        label = rec.get("label") or name
        reason = rec.get("unavailable_reason")
        details = rec.get("details")
        if not isinstance(details, list):
            details = []
        if reason:
            # Keep the reason AND its actionable detail lines: "unavailable
            # (config-invalid)" alone hides the exact fix the detail carries.
            lines.append(f"• **{label}**: unavailable ({reason})")
            for detail in details:
                lines.append(f"  · {detail}")
            continue
        windows = rec.get("windows") or []
        for detail in details:
            lines.append(f"• **{label}** · {detail}")
        if not windows:
            if not details:
                lines.append(f"• **{label}**: no window data")
            continue
        for w in windows:
            wlabel = w.get("label") or "window"
            used = w.get("used_percent")
            tail = _short_reset(w.get("reset_at"))
            if used is None:
                lines.append(f"• **{label}** · {wlabel}" + (f" (reset {tail})" if tail else ""))
            else:
                try:
                    rem = max(0, min(100, round(100 - float(used))))
                except (TypeError, ValueError):
                    rem = "?"
                lines.append(
                    f"• **{label}** · {wlabel} {rem}%" + (f" (reset {tail})" if tail else "")
                )

    if pf and shown == 0:
        return f"📊 quota: no provider matching '{provider_filter}'."

    lines.append("")
    lines.append("_Run `/quota refresh` to force a re-fetch._")
    return "\n".join(lines)


def _plugin_stamp() -> dict[str, Any]:
    """Installed build info for the widget (manifest version + commit stamp).

    Read here, not in the widget, so a mount costs ONE ``cli.exec`` instead of
    two — the extra round-trip was part of the slow reload after a gateway
    switch.
    """
    out: dict[str, Any] = {"plugin_version": None, "installed_sha": None}
    root = Path(__file__).resolve().parent
    try:
        for line in (root / "plugin.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                out["plugin_version"] = line.split(":", 1)[1].strip().strip("'\"")
                break
    except OSError:
        pass
    try:
        stamp = json.loads((root / "version.json").read_text(encoding="utf-8"))
        if isinstance(stamp, dict):
            out["installed_sha"] = stamp.get("installed_sha")
    except (OSError, ValueError):
        pass
    return out


def _render_quota_json() -> str:
    """Render the full quota cache as JSON for the desktop widget.

    Carries the cache age and the installed build stamp alongside the data so
    the widget can poll the cache (never the network), schedule its own refresh,
    and compare versions without further round-trips.
    """
    cache = read_quota_cache()
    # Ensure consistent shape for widget consumption
    providers = cache.get("providers") or {}
    out = {
        "fetched_at": cache.get("fetched_at"),
        "providers": providers,
        "age_s": quota_cache_age_seconds(),
    }
    out.update(_plugin_stamp())
    return json.dumps(out, indent=2, sort_keys=True)


def quota_command(raw_args: str) -> str:
    """In-session ``/quota`` slash command handler.

    Signature matches Hermes ``register_command``: ``fn(raw_args: str) -> str``.
    """
    raw = (raw_args or "").strip()
    parts = raw.split()
    sub = parts[0].lower() if parts else ""

    if sub in ("help", "-h", "--help", "?"):
        return QUOTA_HELP
    if sub in ("refresh", "--refresh", "-r", "reload"):
        try:
            refresh_quota_cache()
        except Exception as e:  # pragma: no cover - defensive
            return f"⚠️ quota refresh failed: {e}"
        return _render_quota(None)
    if sub:
        # Anything else is treated as a provider filter.
        return _render_quota(sub)
    # Bare /quota: refresh if stale, then show.
    if (quota_cache_age_seconds() or 10**9) > MAX_AGE_S:
        try:
            refresh_quota_cache()
        except Exception:
            pass
    return _render_quota(None)


# -- CLI command (hermes quota ...) -----------------------------------------

def setup_argparse(subparser):
    subs = subparser.add_subparsers(dest="quota_command")
    status_p = subs.add_parser("status", help="Show per-provider quota (default)")
    status_p.add_argument("--json", action="store_true", help="Emit raw JSON for the desktop widget")
    status_p.add_argument(
        "--version-json",
        dest="version_json",
        action="store_true",
        help="Emit the installed build stamp (update self-check)",
    )
    status_p.add_argument(
        "--max-age",
        dest="max_age",
        type=int,
        default=None,
        help="Refresh automatically when the cache is older than N seconds",
    )
    status_p.add_argument(
        "--cached",
        action="store_true",
        help="Never refresh: emit the cache as-is (desktop widget poll path)",
    )
    subs.add_parser("refresh", help="Force a re-fetch of all providers")
    prov = subs.add_parser("provider", help="Show quota for one provider")
    prov.add_argument("name", help="provider id, e.g. anthropic, grok, openai-codex")
    for provider_id in _PROVIDER_IDS:
        subs.add_parser(provider_id, help=f"Show quota for {provider_id}")
    _setup_monitor_argparse(subs)
    subparser.set_defaults(func=_handle_cli)


def _setup_monitor_argparse(subs) -> None:
    """``hermes quota monitor <view>``: one JSON object per call (docs/monitor.md)."""
    mon = subs.add_parser("monitor", help="Agent Monitor views as JSON (live, health, usage, kanban, history)")
    msubs = mon.add_subparsers(dest="monitor_command")
    snap = msubs.add_parser("snapshot", help="Everything the widget shows in one poll")
    hist = msubs.add_parser("history", help="Quota history + burn rate")
    hist.add_argument("--days", type=int, default=1, help="Days of history (1-31, default 1)")
    rec = msubs.add_parser("recommend", help="Rank the models work can be routed to now")
    rec.add_argument("--prefer-provider", dest="prefer_provider", default=None)
    rec.add_argument("--exclude", dest="exclude", action="append", default=[],
                     help="Provider to leave out (repeatable or comma separated)")
    rec.add_argument("--min-left", dest="min_left", type=int, default=10,
                     help="Minimum %% left in every quota window (default 10)")
    rec.add_argument("--top", type=int, default=3)
    bud = msubs.add_parser("budget", help="Spend today / month to date vs the stored budget")
    setb = msubs.add_parser("set-budget", help="Store the daily / monthly budget (USD, 0 clears)")
    setb.add_argument("--daily", type=float, default=None)
    setb.add_argument("--monthly", type=float, default=None)
    acc = msubs.add_parser("accounts", help="Credential pools, strategies, fallbacks and sign-in gaps (read-only)")
    setp = msubs.add_parser("set-priority", help="Reorder one pooled credential via `hermes auth priority`")
    setp.add_argument("--provider", required=True)
    setp.add_argument("--target", required=True, help="Short pool id of the credential (see `accounts`)")
    setp.add_argument("--priority", required=True, help="New zero-based position (0 = tried first)")
    sets = msubs.add_parser("set-strategy", help="Set a profile's pool strategy via `hermes config set`")
    sets.add_argument("--provider", required=True)
    sets.add_argument("--strategy", required=True, help="fill_first | round_robin | least_used | random")
    for p in (setp, sets):
        # The pool owner / profile to change. `hermes --profile X ...` also selects X when this is omitted
        # (Hermes consumes the first --profile flag as the process profile).
        p.add_argument("--profile", "--target-profile", dest="target_profile", default=None)
    for p in (mon, snap, hist, rec, bud, setb, acc, setp, sets):
        p.add_argument("--json", action="store_true", help="Emit JSON (the only output format)")


def _handle_monitor(args) -> int:
    from . import quota_monitor as qm

    cmd = getattr(args, "monitor_command", None) or "snapshot"
    try:
        if cmd == "snapshot":
            payload = qm.snapshot()
        elif cmd == "history":
            payload = qm.history_payload(getattr(args, "days", 1) or 1)
        elif cmd == "recommend":
            exclude = [p.strip() for raw in (getattr(args, "exclude", None) or []) for p in raw.split(",") if p.strip()]
            payload = qm.recommend_payload(getattr(args, "prefer_provider", None) or None, exclude,
                                           getattr(args, "min_left", 10), getattr(args, "top", 3))
        elif cmd == "budget":
            payload = qm.budget_payload()
        elif cmd == "set-budget":
            payload = qm.set_budget(getattr(args, "daily", None), getattr(args, "monthly", None))
        elif cmd == "accounts":
            payload = qm.accounts_view()
        elif cmd == "set-priority":
            payload = qm.set_priority(args.provider, args.target, args.priority, getattr(args, "target_profile", None))
        elif cmd == "set-strategy":
            payload = qm.set_strategy(args.provider, args.strategy, getattr(args, "target_profile", None))
        else:
            payload = {"monitor_version": 1, "error": f"unknown monitor command: {cmd}"}
    except Exception as e:  # the widget parses stdout: always answer with one JSON object
        print(json.dumps({"monitor_version": 1, "error": f"{type(e).__name__}: {e}"[:300]}))
        return 1
    print(json.dumps(payload, default=str, separators=(",", ":")))
    return 1 if "error" in payload else 0


def _handle_cli(args):
    cmd = getattr(args, "quota_command", None) or "status"
    if cmd == "monitor":
        return _handle_monitor(args)
    if cmd == "refresh":
        refresh_quota_cache()
        print(_render_quota(None))
        return
    if cmd == "provider":
        print(_render_quota(getattr(args, "name", None)))
        return
    if cmd in _PROVIDER_IDS:
        print(_render_quota(cmd))
        return
    if cmd == "status" and getattr(args, "json", False):
        # Two readers share this payload:
        #  * the widget polls with --cached and wants the cache NOW; its refresh
        #    runs out-of-band so the poll cadence stays exact (blocking the poll
        #    on a provider fetch is what stretched a 60s interval past 100s);
        #  * a plain call with --max-age blocks until the cache is fresh enough.
        if not getattr(args, "cached", False):
            max_age = getattr(args, "max_age", None)
            if max_age is None or max_age <= 0:
                max_age = MAX_AGE_S
            if (quota_cache_age_seconds() or 10**9) > max_age:
                try:
                    refresh_quota_cache()
                except Exception:
                    pass
        print(_render_quota_json())
        return
    if cmd == "status" and getattr(args, "version_json", False):
        # Update self-check: emit the installed build stamp so the widget can
        # compare it against GitHub master without any fs access.
        stamp_path = Path(__file__).resolve().parent / "version.json"
        try:
            print(stamp_path.read_text(encoding="utf-8"))
        except OSError:
            print('{"installed_sha": null}')
        return
    print(_render_quota(None))
