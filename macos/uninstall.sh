#!/bin/sh
# Remove "Quota Notch.app". The quota plugin is not touched.
#   macos/uninstall.sh          stop the app and delete it from ~/Applications (and /Applications if it is there)
#   macos/uninstall.sh --purge  also forget its settings and the status file it writes for the plugin card
set -eu
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(uname -s)" = "Darwin" ] || { echo "nothing to do: Quota Notch is macOS only" >&2; exit 0; }
pkill -x QuotaNotch 2>/dev/null || true
for app in "$HOME/Applications/Quota Notch.app" "/Applications/Quota Notch.app"; do
  if [ -d "$app" ]; then
    rm -rf "$app" && echo "removed: $app"
  fi
done
if [ "$PURGE" = 1 ]; then
  defaults delete local.quota.notch >/dev/null 2>&1 || true
  ROOT="${HERMES_HOME:-$HOME/.hermes}"
  case "$ROOT" in */profiles/*) ROOT="${ROOT%/profiles/*}" ;; esac
  rm -f "$ROOT/quota_notch.json"
  echo "settings and status file removed"
fi
echo "If you had enabled Launch at login, macOS drops the stale login item by itself (System Settings ▸ General ▸ Login Items)."
