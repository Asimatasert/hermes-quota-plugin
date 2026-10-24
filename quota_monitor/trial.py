"""Backend policy for the local, read-only Agent Monitor trial.

None of these boundaries are display preferences: they are enforced in the collection code so that a
widget toggle, a direct CLI call or a direct Python import all meet the same limits.

* monitor data is OFF until ``plugins.entries.quota.settings.monitorEnabled`` is the literal YAML
  boolean ``true`` in the active profile's ``config.yaml`` (strings or 1/0 are not consent);
* only the active profile is in scope, whatever homes a caller passes;
* external agent-CLI stores (Claude Code, Copilot, ...) are never read;
* Kanban lives at the shared Hermes root, so the trial does not read any board;
* agent tools do not register and reject direct calls;
* account and budget mutations are unavailable.

A supported, sanitized host API for credential-pool *metadata* does not exist in this trial, so that
metadata is reported as unavailable instead of being read from ``auth.json`` or reported as "no
credentials".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import hermes_home, read_config

ENROLLMENT_SETTING = "plugins.entries.quota.settings.monitorEnabled"
METADATA_REASON = "no-supported-sanitized-host-api"
EXTERNAL_CLI_REASON = "external-cli-collection-disabled"
KANBAN_REASON = "shared-kanban-disabled"

EXTERNAL_CLI_COLLECTION = False
ACCOUNT_METADATA = False
SHARED_KANBAN = False
AGENT_TOOLS = False
ACCOUNT_MUTATIONS = False
BUDGET_WRITES = False


# Opt-in widening (``monitorScope: all``, default ``active``): every Hermes profile's open sessions in the Live
# view and the agents chip, plus Claude Code / Copilot CLI sessions (their local transcripts and session
# stores, read-only). Only the literal string "all" in the ACTIVE profile's config widens; Kanban, credential
# metadata, agent tools and writes stay unavailable either way.
SCOPE_SETTING = "plugins.entries.quota.settings.monitorScope"
SCOPE_ALL = "all"
SCOPE_ACTIVE = "active"
_SCOPE_TTL_S = 5.0
_scope_cache: Dict[str, Any] = {"at": 0.0, "home": None, "value": SCOPE_ACTIVE}


class TrialError(RuntimeError):
    """An operation the read-only trial refuses; ``code`` is machine readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _value(cfg: Dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def monitor_enrolled(home: Optional[Path] = None) -> bool:
    """Strictly ``true`` in the active profile's config; anything else (including "true"/1) is OFF."""
    cfg = read_config(Path(home) if home is not None else hermes_home())
    return _value(cfg, ENROLLMENT_SETTING) is True


def enrollment_status(home: Optional[Path] = None) -> Dict[str, Any]:
    """The setting's effective state, without echoing unrelated config values."""
    cfg = read_config(Path(home) if home is not None else hermes_home())
    raw = _value(cfg, ENROLLMENT_SETTING)
    return {"enrolled": raw is True, "setting": ENROLLMENT_SETTING,
            "reason": None if raw is True else "monitor-enrollment-off",
            "invalid_value": raw is not None and not isinstance(raw, bool)}


def require_enrollment() -> None:
    if not monitor_enrolled():
        raise TrialError("monitor-enrollment-off",
                         "Agent Monitor collection is off: set %s to true to enroll" % ENROLLMENT_SETTING)


def monitor_scope(home: Optional[Path] = None) -> str:
    """``"all"`` only when enrolled AND ``monitorScope`` is the literal string ``all``; else ``"active"``."""
    import time
    target = Path(home) if home is not None else hermes_home()
    now = time.monotonic()
    try:
        stamp = (target / "config.yaml").stat().st_mtime_ns
    except OSError:
        stamp = None
    key = "%s|%s" % (target, stamp)
    if _scope_cache["home"] == key and now - _scope_cache["at"] < _SCOPE_TTL_S:
        return _scope_cache["value"]
    cfg = read_config(target)
    value = SCOPE_ALL if (_value(cfg, ENROLLMENT_SETTING) is True
                          and _value(cfg, SCOPE_SETTING) == SCOPE_ALL) else SCOPE_ACTIVE
    _scope_cache.update(at=now, home=key, value=value)
    return value


def external_cli_collection() -> bool:
    """Claude Code / Copilot CLI collection: off in the hardened default, on with ``monitorScope: all``."""
    return bool(EXTERNAL_CLI_COLLECTION) or monitor_scope() == SCOPE_ALL


def all_profile_homes() -> List[Tuple[str, Path]]:
    """[("default", root), (name, root/profiles/name), ...]: every profile of this Hermes root."""
    active = hermes_home()
    root = active.parent.parent if active.parent.name == "profiles" else active
    out = [("default", root)]
    try:
        profiles = root / "profiles"
        if profiles.is_dir():
            for p in sorted(profiles.iterdir()):
                if p.is_dir() and not p.is_symlink() and not p.name.startswith("."):
                    out.append((p.name, p))
    except OSError:
        pass
    return out


def live_homes(homes: Optional[Iterable[Tuple[str, Path]]] = None) -> List[Tuple[str, Path]]:
    """Homes the Live view reads: every profile with ``monitorScope: all``, else the active one only."""
    if monitor_scope() == SCOPE_ALL:
        return all_profile_homes()
    return allowed_homes(homes)


def live_home_in_scope(home: Path) -> bool:
    if monitor_scope() == SCOPE_ALL:
        try:
            target = Path(home).resolve()
            return any(target == p.resolve() for _n, p in all_profile_homes())
        except OSError:
            return False
    return home_in_scope(home)


def active_label() -> str:
    """The active profile's real label, independent of any caller-supplied name."""
    active = hermes_home()
    return active.name if active.parent.name == "profiles" else "default"


def scoped_root(root: Optional[Path] = None) -> Path:
    """Return the active root only; a caller-supplied root cannot escape the active profile."""
    active = hermes_home()
    active_root = active.parent.parent if active.parent.name == "profiles" else active
    if root is None:
        return active_root
    try:
        given = Path(root).resolve()
        active_res = active.resolve()
        root_res = active_root.resolve()
    except OSError:
        raise TrialError("monitor-scope-violation", "profile scope could not be resolved")
    if given not in (root_res, active_res):
        raise TrialError("monitor-scope-violation",
                         "Agent Monitor is scoped to the active profile only")
    return active_root


def home_in_scope(home: Path) -> bool:
    """Whether ``home`` is the active profile's home (symlink-aware)."""
    active = hermes_home()
    try:
        return Path(home).resolve() == active.resolve()
    except OSError:
        return Path(home) == active


def allowed_homes(homes: Optional[Iterable[Tuple[str, Path]]] = None) -> List[Tuple[str, Path]]:
    """Clamp any caller-supplied homes to the active profile; a passed sibling cannot widen scope."""
    active = hermes_home()
    try:
        target = active.resolve()
    except OSError:
        target = active
    out: List[Tuple[str, Path]] = []
    actual = active_label()
    for _name, path in homes or []:
        candidate = Path(path)
        try:
            same = candidate.resolve() == target
        except OSError:
            same = False
        if same:
            out.append((actual, candidate))
    if out:
        return out
    return [(actual, active)]
