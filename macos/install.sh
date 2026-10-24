#!/bin/sh
# Build and install "Quota Notch.app" into ~/Applications (optional macOS companion of the quota plugin).
# Separate from the plugin's own install.sh on purpose: the plugin works without it, on every OS.
#   macos/install.sh            build, install, (re)start
#   macos/install.sh --no-open  build and install only
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$HOME/Applications"
APP="$DEST_DIR/Quota Notch.app"
OPEN=1
for a in "$@"; do
  case "$a" in
    --no-open) OPEN=0 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
[ "$(uname -s)" = "Darwin" ] || { echo "Quota Notch is macOS only; the quota plugin itself does not need it." >&2; exit 2; }

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/quota-notch-install.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
OUT="$STAGE" "$HERE/build.sh"

WAS_RUNNING=0
if pgrep -x QuotaNotch >/dev/null 2>&1; then
  WAS_RUNNING=1
  pkill -x QuotaNotch || true
  sleep 1
fi
mkdir -p "$DEST_DIR"
rm -rf "$APP"
ditto "$STAGE/Quota Notch.app" "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
echo "installed: $APP"
if [ "$OPEN" = 1 ] || [ "$WAS_RUNNING" = 1 ]; then
  open "$APP"
  echo "started (menu bar / notch). Launch at login: Settings ▸ General."
fi
