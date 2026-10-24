"""Quota Notch: facts about the optional macOS notch / menu bar companion (``macos/`` in this repo).

``hermes quota monitor companion --json`` answers the Desktop widget's Settings card:

* ``platform``: ``sys.platform`` of the backend (the companion only exists on macOS);
* ``installed`` / ``app_path``: ``Quota Notch.app`` in ``~/Applications`` or ``/Applications``;
* ``running``: ``pgrep -x QuotaNotch`` (fixed argv, macOS only);
* ``notch`` / ``status``: what the app itself reported in ``<hermes root>/quota_notch.json``
  (the renderer cannot know whether this Mac has a notch; the native app can);
* ``install_script`` / ``install_command``: ``<plugin dir>/macos/install.sh`` when it shipped with the plugin.

``--open`` starts the installed app with ``open -a <app>`` (macOS only, fixed argv, no shell).
Nothing here needs monitor enrollment: no session, usage or credential data is read.
Stdlib only, Python 3.9 compatible.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .common import MONITOR_VERSION, hermes_root

APP_NAME = "Quota Notch.app"
EXECUTABLE = "QuotaNotch"
STATUS_FILE = "quota_notch.json"
REPO_URL = "https://github.com/rarf/hermes-quota-plugin"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
STATUS_MAX_BYTES = 4096


def app_candidates(home: Optional[Path] = None) -> List[Path]:
    home = Path.home() if home is None else Path(home)
    return [home / "Applications" / APP_NAME, Path("/Applications") / APP_NAME]


def find_app(home: Optional[Path] = None) -> Optional[Path]:
    for p in app_candidates(home):
        if (p / "Contents" / "Info.plist").is_file():
            return p
    return None


def read_status(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The app's own report, whitelisted and type-checked (it is a plain user-writable file)."""
    path = (hermes_root() if root is None else Path(root)) / STATUS_FILE
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > STATUS_MAX_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {}
    if isinstance(raw.get("version"), str):
        out["version"] = raw["version"][:32]
    if isinstance(raw.get("notch"), bool):
        out["notch"] = raw["notch"]
    since = raw.get("running_since")
    if isinstance(since, (int, float)) and not isinstance(since, bool) and since > 0:
        out["running_since"] = float(since)
    if raw.get("display") in ("both", "island", "menubar"):
        out["display"] = raw["display"]
    return out


def is_running(run: Callable[..., Any] = subprocess.run) -> Optional[bool]:
    if sys.platform != "darwin":
        return None
    try:
        res = run(["/usr/bin/pgrep", "-x", EXECUTABLE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.returncode == 0


def install_script(root: Optional[Path] = None) -> Optional[Path]:
    p = (PLUGIN_ROOT if root is None else Path(root)) / "macos" / "install.sh"
    return p if p.is_file() else None


def info(home: Optional[Path] = None, root: Optional[Path] = None, plugin_root: Optional[Path] = None,
         run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    mac = sys.platform == "darwin"
    app = find_app(home) if mac else None
    status = read_status(root) if mac else None
    script = install_script(plugin_root) if mac else None
    running = is_running(run) if mac else None
    return {
        "monitor_version": MONITOR_VERSION,
        "generated_at": time.time(),
        "platform": sys.platform,
        "supported": mac,
        "installed": bool(app),
        "app_path": str(app) if app else None,
        "running": running,
        # Only the app knows; None until it has run once on this Mac.
        "notch": status.get("notch") if status else None,
        "status": status,
        "install_script": str(script) if script else None,
        "install_command": shlex.quote(str(script)) if script else None,
        "uninstall_command": shlex.quote(str(script.parent / "uninstall.sh")) if script else None,
        "repo_url": REPO_URL,
    }


def open_app(home: Optional[Path] = None, run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    out = info(home=home, run=run)
    if sys.platform != "darwin":
        out.update(opened=False, error="Quota Notch is macOS only", error_code="companion-unsupported")
        return out
    app = find_app(home)
    if app is None:
        out.update(opened=False, error="Quota Notch is not installed", error_code="companion-not-installed")
        return out
    try:
        res = run(["/usr/bin/open", "-a", str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  timeout=15, check=False)
        ok = res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    out["opened"] = ok
    if not ok:
        out.update(error="open -a failed", error_code="companion-open-failed")
    return out
