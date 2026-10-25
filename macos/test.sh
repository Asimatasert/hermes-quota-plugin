#!/bin/sh
# Quota Notch unit tests: decoding of the plugin's JSON shapes (synthetic fixtures), session grouping, the
# click rule, the event state machine and the CLI runner (against Tests/fake-hermes). Builds in a temp dir and
# installs nothing. Used by the optional macOS CI job.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
Q="$HERE/QuotaNotch"
T="$(mktemp -d "${TMPDIR:-/tmp}/quota-notch-tests.XXXXXX")"
trap 'rm -rf "$T"' EXIT
ARCH="$(uname -m)"
chmod +x "$Q/Tests/fake-hermes" 2>/dev/null || true
swiftc -O -swift-version 5 -target "$ARCH-apple-macos14" \
  "$Q/Sources/Model.swift" "$Q/Sources/CLI.swift" "$Q/Sources/Prefs.swift" "$Q/Tests/main.swift" -o "$T/tests"
"$T/tests" "$Q/Tests/Fixtures" "$Q/Tests/fake-hermes"
