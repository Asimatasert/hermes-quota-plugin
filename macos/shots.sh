#!/bin/sh
# Screenshots of Quota Notch on SYNTHETIC data, without touching an installed copy:
# a separate bundle id (local.quota.notch.shots, own settings), a fake `hermes` that prints Tests/Fixtures,
# a scratch HERMES_HOME (the status file lands there), windows one level above an installed notch app.
#   macos/shots.sh <out_dir>
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:?usage: macos/shots.sh <out_dir>}"
mkdir -p "$OUT"
B="$(mktemp -d "${TMPDIR:-/tmp}/quota-notch-shots.XXXXXX")"
trap 'rm -rf "$B"' EXIT
ARCHS="$(uname -m)" OUT="$B" BUNDLE_ID=local.quota.notch.shots APP_NAME="Quota Notch Shots" EXE=QuotaNotchShots \
  HOOKS="$HERE/QuotaNotch/Tests/ShotsHooks.swift" "$HERE/build.sh"
mkdir -p "$B/home"
defaults delete local.quota.notch.shots >/dev/null 2>&1 || true
# the fake runs from the temp dir (its path shows up in Settings ▸ Data source)
mkdir -p "$B/fake"
cp -R "$HERE/QuotaNotch/Tests/Fixtures" "$B/fake/Fixtures"
cp "$HERE/QuotaNotch/Tests/fake-hermes" "$B/fake/hermes"
chmod +x "$B/fake/hermes"
QUOTA_NOTCH_SHOTS="$OUT" QUOTA_NOTCH_HERMES="$B/fake/hermes" QUOTA_NOTCH_LEVEL_BOOST=1 HERMES_HOME="$B/home" \
  "$B/Quota Notch Shots.app/Contents/MacOS/QuotaNotchShots"
cp "$B/home/quota_notch.json" "$OUT/status-file.json" 2>/dev/null || true
# monitor enrollment off: the one-line hint with the exact command (menu bar window only)
FAKE_SNAPSHOT=snapshot-off.json QUOTA_NOTCH_SHOTS="$OUT" QUOTA_NOTCH_SHOTS_ONLY=menubar-off QUOTA_NOTCH_HERMES="$B/fake/hermes" \
  QUOTA_NOTCH_LEVEL_BOOST=1 HERMES_HOME="$B/home" "$B/Quota Notch Shots.app/Contents/MacOS/QuotaNotchShots"
defaults delete local.quota.notch.shots >/dev/null 2>&1 || true
echo "screenshots: $OUT"
