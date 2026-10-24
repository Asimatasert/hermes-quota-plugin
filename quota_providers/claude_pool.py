"""Opt-in: one Anthropic card per ACCOUNT behind Hermes' own credential pool.

``plugins.entries.quota.settings.claudePoolAccounts: true`` (default off) makes the Anthropic fetcher read
every row of Hermes' ``anthropic`` credential pool, the pool Hermes rotates through for Claude requests. The
grouping is ported from Agent Monitor (``panel.py``): rows of the same Claude account (a Hermes PKCE login and
the ``claude_code`` login, say) collapse into one card. Rules:

* **Read-only.** Rows come from Hermes' profile-aware reader (``hermes_cli.auth.read_credential_pool``) or,
  without the core, from the profile/global ``auth.json`` snapshot. Nothing is written, selected, rotated or
  refreshed; refresh tokens are never read or exchanged.
* **Stored access token only, while unexpired.** A row whose ``expires_at_ms`` has passed (or passes within a
  minute) is never sent anywhere: Hermes refreshes it the next time it uses that credential.
* **Account identity** (``claudePoolIdentity``, on with pool accounts unless set to ``false``): one
  ``GET https://api.anthropic.com/api/oauth/profile`` per access token (the call Claude Code itself makes),
  cached for the token's life in the shared state file ``quota_anthropic_identity.json`` as a one-way key
  (``sha256(account.uuid)[:16]``) by token digest and pool-row slot digest; 401/403 are not repeated for 30
  minutes; a 429 opens that token's usage cooldown. Without identity, rows are grouped by exact token only.
* **Usage requests.** One credential per account, in pool order, through the existing shared payload cache,
  per-token 429 cooldown and request lock, sequentially inside the multi-account deadline.
* **Cache contents.** Like Codex/CommandCode pool cards: the account's display label (the first saved pool
  label in pool order, skipping the ``claude_code`` login; sanitized), the pool position/size/strategy and
  credential counts. Never tokens, digests, account keys, sources, paths or e-mail addresses.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from typing import Any, Optional

_PROVIDER = "anthropic"
SETTING = "claudePoolAccounts"
MAX_POOL_ROWS = 8
EXPIRY_SKEW_S = 60
TOKEN_EXPIRED_REASON = "token-expired"
TOKEN_EXPIRED_DETAIL = (
    "Stored access token expired · Hermes refreshes it on next use; "
    "this plugin never refreshes tokens."
)
_GENERIC_LABELS = {"oauth", "default", "hermes_pkce", "manual", "pkce", "claude_code", "claude-code"}
_STRATEGIES = {"fill_first", "round_robin", "least_used", "random"}


def display_label(value: Any) -> str:
    """Strip control/format characters and bound a saved label (display only)."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if unicodedata.category(c) not in
                   {"Cc", "Cf", "Cs", "Zl", "Zp"}).strip()[:64].rstrip()


def _settings() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception:  # noqa: BLE001 - standalone install / unreadable config
        return {}
    plugins = config.get("plugins") if isinstance(config, dict) else None
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get("quota") if isinstance(entries, dict) else None
    settings = entry.get("settings") if isinstance(entry, dict) else None
    out = dict(settings) if isinstance(settings, dict) else {}
    strategies = config.get("credential_pool_strategies") if isinstance(config, dict) else None
    out["__strategy__"] = strategies.get(_PROVIDER) if isinstance(strategies, dict) else None
    return out


def enabled(settings: Optional[dict[str, Any]] = None) -> bool:
    """Only a real boolean ``true`` (or an affirmative string) opts in."""
    from .base import opt_in_flag

    settings = _settings() if settings is None else settings
    return opt_in_flag(settings.get(SETTING))


IDENTITY_SETTING = "claudePoolIdentity"


def identity_enabled(settings: Optional[dict[str, Any]] = None) -> bool:
    """Account lookup via Anthropic's OAuth profile endpoint: on with pool accounts unless set to false."""
    settings = _settings() if settings is None else settings
    value = settings.get(IDENTITY_SETTING)
    return True if value is None else value is True or (isinstance(value, str) and value.strip().lower() in
                                                         {"1", "true", "yes", "on"})


def strategy(settings: Optional[dict[str, Any]] = None) -> str:
    settings = _settings() if settings is None else settings
    value = settings.get("__strategy__")
    return value if isinstance(value, str) and value in _STRATEGIES else "fill_first"


def _rows_from_store() -> list[dict[str, Any]]:
    """Profile pool, else the global one, straight from ``auth.json`` (no writes)."""
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home
    except Exception:  # noqa: BLE001 - core absent
        return []
    seen: list[str] = []
    for home in (get_hermes_home(), get_default_hermes_root()):
        key = str(home)
        if key in seen:
            continue
        seen.append(key)
        try:
            with open(os.path.join(key, "auth.json"), "r", encoding="utf-8-sig") as fh:
                store = json.load(fh)
        except (OSError, ValueError, UnicodeError):
            continue
        pool = store.get("credential_pool") if isinstance(store, dict) else None
        rows = pool.get(_PROVIDER) if isinstance(pool, dict) else None
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
    return []


def pool_rows() -> list[dict[str, Any]]:
    """Saved ``anthropic`` pool rows, profile-aware, without selecting/rotating."""
    try:
        from hermes_cli.auth import read_credential_pool

        return [row for row in (read_credential_pool(_PROVIDER) or []) if isinstance(row, dict)]
    except Exception:  # noqa: BLE001 - core absent or store unreadable
        return _rows_from_store()


def _priority(row: dict[str, Any]) -> float:
    value = row.get("priority")
    if isinstance(value, bool):
        return math.inf
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return float(value)
    return math.inf


def _expired(row: dict[str, Any], now: float) -> bool:
    value = row.get("expires_at_ms")
    if value is None or isinstance(value, bool):
        return False  # unknown expiry: the endpoint decides, never refresh here
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(ms):
        return False
    return ms / 1000.0 <= now + EXPIRY_SKEW_S


def _row_id(row: dict[str, Any], index: int) -> str:
    raw = row.get("id")
    if isinstance(raw, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw.strip()):
        return "pool-" + raw.strip()
    return "pool-%d" % (index + 1)


def _row_label(row: dict[str, Any], index: int) -> str:
    label = display_label(row.get("label"))
    if label and label.lower() not in _GENERIC_LABELS:
        return label
    if row.get("source") == "claude_code":
        return "Claude Code"
    return "Account %d" % (index + 1)


def credentials(now: Optional[float] = None, rows: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Ordered pool credentials: [{id, label, token, expired, priority, size}].

    Order is Hermes' own (ascending ``priority``, ties keep saved order). Rows
    without an access token are skipped; byte-identical tokens collapse to the
    best-priority row. ``token`` lives only in the returned in-memory list.
    """
    now = time.time() if now is None else now
    rows = pool_rows() if rows is None else rows
    ordered = sorted((r for r in rows if isinstance(r, dict)), key=_priority)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(ordered):
        token = row.get("access_token")
        if not isinstance(token, str) or not token.strip():
            continue
        token = token.strip()
        if token in seen:
            continue
        seen.add(token)
        row_id = _row_id(row, index)
        out.append({
            "id": row_id,
            "label": _row_label(row, len(out)),
            "named": bool(display_label(row.get("label"))) and row.get("source") != "claude_code"
            and display_label(row.get("label")).lower() not in _GENERIC_LABELS,
            "source": "claude_code" if row.get("source") == "claude_code" else "pool",
            "slot": row_id,
            "token": token,
            "expired": _expired(row, now),
            "order": len(out) + 1,
        })
        if len(out) >= MAX_POOL_ROWS:
            break
    for cred in out:
        cred["size"] = len(out)
    return out


def pool_meta(cred: dict[str, Any], strategy_name: str) -> dict[str, Any]:
    """Cache-safe pool position: order, size and the profile's strategy."""
    return {"priority": int(cred["order"]), "size": int(cred["size"]), "strategy": strategy_name}


# ---------- grouping by account (ported from Agent Monitor's _group_anthropic) ----------

def windows_sig(windows: list[Any]) -> tuple:
    """Fingerprint of usage windows: equal only when every window matches to the digit."""
    out = []
    for w in windows:
        reset = getattr(w, "reset_at", None)
        try:
            from datetime import datetime
            ts = round(datetime.fromisoformat(str(reset).replace("Z", "+00:00")).timestamp()) if reset else 0
        except ValueError:
            ts = 0
        used = getattr(w, "used_percent", None)
        out.append((str(getattr(w, "label", "")), round(float(used), 4) if used is not None else None, ts))
    return tuple(sorted(out, key=repr))


def group_credentials(creds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group credentials by the account behind them -> [{"key", "creds": [...], "wins", "err"}].

    ``creds``: [{..., "key": account key | None, "wins": [QuotaWindow] , "details": [...], "err": str | None}].
    1. identity readable -> grouped by account key ("verified");
    2. identity unreadable but usage readable -> joins the group whose windows are identical to the digit
       ("same_windows"), else its own group keyed by a windows hash ("windows_only");
    3. nothing readable -> never its own card: listed "unverified" under the first group, or, when no
       credential is readable at all, every credential under one plain group (key None).
    """
    import hashlib

    groups: dict[str, dict[str, Any]] = {}
    unreadable: list[dict[str, Any]] = []
    for c in creds:
        if c.get("key"):
            g = groups.setdefault(c["key"], {"creds": [], "wins": [], "details": [], "err": None})
            g["creds"].append(dict(c, status_of="verified"))
            if c.get("wins") and not g["wins"]:
                g["wins"], g["details"] = c["wins"], c.get("details") or []
            if c.get("err") and not c.get("wins"):
                g["err"] = g["err"] or c["err"]
    for c in creds:
        if c.get("key"):
            continue
        if c.get("wins"):
            sig = windows_sig(c["wins"])
            hit = next((g for g in groups.values() if g["wins"] and windows_sig(g["wins"]) == sig), None)
            if hit is not None:
                hit["creds"].append(dict(c, status_of="same_windows"))
            else:
                k = "w" + hashlib.sha256(repr(sig).encode()).hexdigest()[:7]
                g = groups.setdefault(k, {"creds": [], "wins": c["wins"], "details": c.get("details") or [],
                                          "err": None})
                g["creds"].append(dict(c, status_of="same_windows" if g["creds"] else "windows_only"))
        else:
            unreadable.append(c)
    if unreadable and not groups:
        return [{"key": None, "creds": [dict(c, status_of="unverified") for c in unreadable], "wins": [],
                 "details": [], "err": next((c["err"] for c in unreadable if c.get("err")), None)}]
    if unreadable:
        first = next(iter(groups.values()))
        first["creds"] += [dict(c, status_of="unverified") for c in unreadable]
    return [dict(g, key=k) for k, g in groups.items()]


def account_name(creds: list[dict[str, Any]]) -> str:
    """The label of the account's first credential (pool order), skipping the Claude Code login and
    credentials not verified to be this account (Agent Monitor's account_name)."""
    mine = sorted((c for c in creds if c.get("status_of") != "unverified"), key=lambda c: c.get("order", 0))
    named = [c for c in mine if c.get("named")]
    pick = (named or mine or [{}])[0]
    return pick.get("label") or "Account"
