"""Shared helpers for the quota monitor: Hermes paths, read-only sqlite, small parsers.

Every Hermes file is opened read-only (sqlite ``mode=ro`` URIs, plain reads for json/yaml). The only
files this package writes are its own ``quota_monitor.db`` / ``quota_monitor.json`` (see ``store``).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

MONITOR_VERSION = 1


# ---------- Hermes paths ----------

def hermes_home() -> Path:
    """The active Hermes home (a profile dir when Hermes runs with ``--profile``)."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        env = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(env).expanduser() if env else Path.home() / ".hermes"


def hermes_root(home: Optional[Path] = None) -> Path:
    """The Hermes root that holds ``profiles/``: ``<root>/profiles/<name>`` -> ``<root>``."""
    home = Path(home) if home is not None else hermes_home()
    if home.parent.name == "profiles":
        return home.parent.parent
    return home


def profile_homes(root: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """[("default", root), (name, root/profiles/name), ...] — every profile on this machine."""
    root = Path(root) if root is not None else hermes_root()
    out = [("default", root)]
    profiles = root / "profiles"
    try:
        if profiles.is_dir():
            for p in sorted(profiles.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    out.append((p.name, p))
    except OSError:
        pass
    return out


def kanban_dbs(root: Optional[Path] = None) -> List[Tuple[str, Path]]:
    root = Path(root) if root is not None else hermes_root()
    dbs = [("default", root / "kanban.db")]
    boards = root / "kanban" / "boards"
    try:
        if boards.is_dir():
            dbs += [(b.name, b / "kanban.db") for b in sorted(boards.iterdir()) if (b / "kanban.db").exists()]
    except OSError:
        pass
    return dbs


# ---------- sqlite ----------

@contextmanager
def ro(db: Path) -> Iterator[sqlite3.Connection]:
    """Read-only connection that is always closed (sqlite3's own ``with`` only commits)."""
    conn = sqlite3.connect("file:%s?mode=ro" % quote(str(db)), uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def table_cols(c: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}


# ---------- small parsers ----------

def num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def iso_ts(s: Any) -> Optional[float]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def day_range(d_from: date, d_to: date) -> Tuple[float, float, List[str]]:
    """Local [start, end) timestamps for the inclusive day range + the list of ISO days."""
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    start = datetime.combine(d_from, datetime.min.time()).timestamp()
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time()).timestamp()
    days = [(d_from + timedelta(days=i)).isoformat() for i in range((d_to - d_from).days + 1)]
    return start, end, days


def read_config(home: Path) -> Dict[str, Any]:
    """A profile's config.yaml (read-only), {} when unreadable or no YAML parser is available."""
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        from ruamel.yaml import YAML
        return YAML(typ="safe").load(text) or {}
    except ImportError:
        pass
    except Exception:
        return {}
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False
    return True


def cost_expr(prefix: str = "") -> str:
    return "coalesce(%sactual_cost_usd, %sestimated_cost_usd, 0)" % (prefix, prefix)


def usage_cost_expr(prefix: str = "") -> str:
    """Cost of a ``session_model_usage`` row. Its cost columns are NOT NULL DEFAULT 0, so a plain coalesce would
    always pick ``actual_cost_usd`` = 0; 0 means "unknown" here and falls through to the session split."""
    return "coalesce(nullif(%sactual_cost_usd, 0), nullif(%sestimated_cost_usd, 0), 0)" % (prefix, prefix)


def session_costs(c: sqlite3.Connection, ids: Any = None) -> Dict[str, Tuple[float, int]]:
    """{session_id: (cost, tokens)} — model usage rows can carry no cost, so a session's cost is split across
    its models by token share."""
    sql = "SELECT id, %s cost, coalesce(input_tokens,0)+coalesce(output_tokens,0) tok FROM sessions" % cost_expr()
    params: List[Any] = []
    if ids is not None:
        params = list(ids)
        if not params:
            return {}
        out: Dict[str, Tuple[float, int]] = {}
        for i in range(0, len(params), 500):
            part = params[i:i + 500]
            for r in c.execute(sql + " WHERE id IN (%s)" % ",".join("?" * len(part)), part):
                out[r["id"]] = (r["cost"], r["tok"])
        return out
    return {r["id"]: (r["cost"], r["tok"]) for r in c.execute(sql)}


def row_cost(row_cost_value: Any, row_tok: int, sess: Optional[Tuple[float, int]]) -> float:
    if row_cost_value:
        return row_cost_value
    if not sess or not sess[0]:
        return 0.0
    cost, tok = sess
    return cost * (row_tok / tok) if tok else 0.0
