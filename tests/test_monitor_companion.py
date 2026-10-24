"""`hermes quota monitor companion`: facts about the optional macOS app (synthetic homes, no real processes).

Run from the repo root:  python -m unittest tests.test_monitor_companion
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from monitor_fixtures import load

companion = load("quota_monitor.companion")
commands = load("commands")


class _Run:
    """Records argv; answers with a fixed return code (never starts a process)."""

    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs = kwargs
        return subprocess.CompletedProcess(argv, self.rc)


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.root = base / "hermes"
        self.plugin = base / "plugin"
        for d in (self.home, self.root, self.plugin / "macos"):
            d.mkdir(parents=True)
        (self.plugin / "macos" / "install.sh").write_text("#!/bin/sh\n")
        (self.plugin / "macos" / "uninstall.sh").write_text("#!/bin/sh\n")
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def install_app(self):
        app = self.home / "Applications" / "Quota Notch.app" / "Contents"
        app.mkdir(parents=True)
        (app / "Info.plist").write_text("<plist/>")
        return app.parent

    def write_status(self, payload):
        (self.root / "quota_notch.json").write_text(json.dumps(payload) if not isinstance(payload, str) else payload)

    def info(self, platform="darwin", rc=0):
        run = _Run(rc)
        with mock.patch.object(companion.sys, "platform", platform):
            out = companion.info(home=self.home, root=self.root, plugin_root=self.plugin, run=run)
        return out, run

    def test_mac_installed_running_with_notch(self):
        app = self.install_app()
        self.write_status({"version": "1.0.0", "notch": True, "running_since": 1800000000.5, "display": "both", "pid": 42})
        out, run = self.info()
        self.assertTrue(out["supported"])
        self.assertTrue(out["installed"])
        self.assertEqual(out["app_path"], str(app))
        self.assertTrue(out["running"])
        self.assertIs(out["notch"], True)
        self.assertEqual(out["status"], {"version": "1.0.0", "notch": True, "running_since": 1800000000.5, "display": "both"})
        self.assertEqual(run.calls, [["/usr/bin/pgrep", "-x", "QuotaNotch"]])
        self.assertTrue(out["install_command"].endswith("macos/install.sh"))
        self.assertEqual(out["monitor_version"], 1)

    def test_mac_not_installed_never_ran(self):
        out, _ = self.info(rc=1)
        self.assertFalse(out["installed"])
        self.assertIsNone(out["app_path"])
        self.assertFalse(out["running"])
        self.assertIsNone(out["notch"], "unknown until the app has run once")
        self.assertIsNotNone(out["install_script"])

    def test_no_notch_reported(self):
        self.write_status({"version": "1.0.0", "notch": False})
        self.assertIs(self.info()[0]["notch"], False)

    def test_other_platforms_report_unsupported_and_spawn_nothing(self):
        self.install_app()
        self.write_status({"notch": True})
        out, run = self.info(platform="linux")
        self.assertFalse(out["supported"])
        self.assertFalse(out["installed"])
        self.assertIsNone(out["running"])
        self.assertIsNone(out["notch"])
        self.assertIsNone(out["install_command"])
        self.assertEqual(run.calls, [])

    def test_status_file_is_whitelisted(self):
        self.write_status({"notch": "yes", "version": 3, "running_since": True, "display": "elsewhere",
                           "secret": "x" * 10})
        self.assertEqual(companion.read_status(self.root), {})
        self.write_status("[1, 2]")
        self.assertIsNone(companion.read_status(self.root))
        self.write_status("{not json")
        self.assertIsNone(companion.read_status(self.root))
        self.write_status({"notch": True, "pad": "x" * 5000})
        self.assertIsNone(companion.read_status(self.root), "oversized file ignored")

    def test_status_symlink_rejected(self):
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps({"notch": True}))
        (self.root / "quota_notch.json").symlink_to(target)
        self.assertIsNone(companion.read_status(self.root))

    def test_missing_install_script(self):
        (self.plugin / "macos" / "install.sh").unlink()
        out, _ = self.info()
        self.assertIsNone(out["install_command"])
        self.assertEqual(out["repo_url"], "https://github.com/rarf/hermes-quota-plugin")

    def test_install_command_is_shell_quoted(self):
        spaced = Path(self.tmp.name) / "my plugins"
        (spaced / "macos").mkdir(parents=True)
        (spaced / "macos" / "install.sh").write_text("#!/bin/sh\n")
        with mock.patch.object(companion.sys, "platform", "darwin"):
            out = companion.info(home=self.home, root=self.root, plugin_root=spaced, run=_Run())
        self.assertTrue(out["install_command"].startswith("'") and out["install_command"].endswith("install.sh'"))

    def test_open_uses_fixed_argv(self):
        app = self.install_app()
        run = _Run(0)
        with mock.patch.object(companion.sys, "platform", "darwin"):
            out = companion.open_app(home=self.home, run=run)
        self.assertTrue(out["opened"])
        self.assertIn(["/usr/bin/open", "-a", str(app)], run.calls)
        self.assertNotIn("shell", run.kwargs)

    def test_open_refusals(self):
        run = _Run(0)
        with mock.patch.object(companion.sys, "platform", "darwin"):
            out = companion.open_app(home=self.home, run=run)
        self.assertEqual(out["error_code"], "companion-not-installed")
        self.assertNotIn("/usr/bin/open", [c[0] for c in run.calls])
        with mock.patch.object(companion.sys, "platform", "linux"):
            out = companion.open_app(home=self.home, run=run)
        self.assertEqual(out["error_code"], "companion-unsupported")
        self.install_app()
        with mock.patch.object(companion.sys, "platform", "darwin"):
            out = companion.open_app(home=self.home, run=_Run(1))
        self.assertFalse(out["opened"])
        self.assertEqual(out["error_code"], "companion-open-failed")

    def test_cli_subcommand_prints_one_json_object(self):
        parser = argparse.ArgumentParser()
        commands.setup_argparse(parser)
        for argv, fn in ((["monitor", "companion", "--json"], "info"), (["monitor", "companion", "--open", "--json"], "open_app")):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                fake = {"monitor_version": 1, "generated_at": 1.0, "platform": "darwin"}
                with mock.patch.object(companion, fn, return_value=fake) as called:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        rc = args.func(args)
                called.assert_called_once_with()
                self.assertIn(rc, (0, None))
                self.assertEqual(json.loads(buf.getvalue()), fake)

    def test_cli_works_without_monitor_enrollment(self):
        # no config.yaml in the synthetic root: the monitor is not enrolled, the companion still answers
        parser = argparse.ArgumentParser()
        commands.setup_argparse(parser)
        args = parser.parse_args(["monitor", "companion", "--json"])
        buf = io.StringIO()
        with mock.patch.object(companion, "is_running", return_value=None), contextlib.redirect_stdout(buf):
            args.func(args)
        payload = json.loads(buf.getvalue())
        self.assertNotIn("error", payload)
        self.assertIn("platform", payload)


if __name__ == "__main__":
    unittest.main()
