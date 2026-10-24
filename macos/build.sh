#!/bin/sh
# Build "Quota Notch.app" (optional macOS notch / menu bar companion of the quota plugin) with swiftc — no Xcode
# project, no signing identity. Installs nothing: run macos/install.sh for that.
#
#   macos/build.sh                 → macos/build/Quota Notch.app (universal arm64 + x86_64 when both SDK slices build)
#   ARCHS=arm64 macos/build.sh     → one architecture only (faster)
#
# Test / screenshot builds override the identity so they never share settings with an installed copy:
#   BUNDLE_ID=local.quota.notch.shots APP_NAME="Quota Notch Shots" EXE=QuotaNotchShots HOOKS=<file.swift> OUT=<dir>
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/QuotaNotch"
OUT="${OUT:-$HERE/build}"
BUNDLE_ID="${BUNDLE_ID:-local.quota.notch}"
APP_NAME="${APP_NAME:-Quota Notch}"
EXE="${EXE:-QuotaNotch}"
HOOKS="${HOOKS:-$SRC/Sources/Hooks.swift}"
ARCHS="${ARCHS:-arm64 x86_64}"
VERSION="$(tr -d ' \n' < "$SRC/VERSION")"

[ "$(uname -s)" = "Darwin" ] || { echo "Quota Notch is macOS only." >&2; exit 2; }
command -v swiftc >/dev/null || { echo "swiftc not found: install the Xcode Command Line Tools (xcode-select --install)." >&2; exit 2; }

APP="$OUT/$APP_NAME.app"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/quota-notch-build.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
SOURCES=""
for f in "$SRC"/Sources/*.swift; do
  [ "$(basename "$f")" = "Hooks.swift" ] && continue
  SOURCES="$SOURCES $f"
done

slices=""
for arch in $ARCHS; do
  # shellcheck disable=SC2086
  if swiftc -O -parse-as-library -swift-version 5 -target "$arch-apple-macos14" $SOURCES "$HOOKS" \
       -o "$TMP/$EXE-$arch" 2> "$TMP/err-$arch.txt"; then
    slices="$slices $TMP/$EXE-$arch"
  else
    echo "warning: $arch slice failed to build:" >&2
    tail -20 "$TMP/err-$arch.txt" >&2
  fi
done
[ -n "$slices" ] || { echo "build failed" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# shellcheck disable=SC2086
lipo -create $slices -output "$APP/Contents/MacOS/$EXE"
cp -R "$SRC/Resources/icons" "$APP/Contents/Resources/icons"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>$EXE</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT — part of hermes-quota-plugin</string>
</dict></plist>
PL
codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "warning: ad-hoc codesign failed" >&2
echo "built: $APP ($(lipo -archs "$APP/Contents/MacOS/$EXE"))"
