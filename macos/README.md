# Quota Notch (optional macOS companion)

A small native Mac app that shows the quota plugin's data **outside the Hermes window**:

- **Notch island** (MacBooks with a notch): hidden until you hover the notch, or always-on "ears" with quota
  rings on the left and the working-session count on the right. The ears peek when a quota moves or a session
  starts; alerts pop down from the notch (needs approval, your turn, may be stuck, quota 80 % / 95 %, filling up
  soon). Hovering opens a panel with every account's quota rings and reset countdowns, today's estimated cost,
  and the open sessions: the session in use **now** pinned first, then the others grouped by Hermes profile,
  Claude Code and Copilot CLI.
- **Menu bar extra**: the same panel, for Macs without a notch or by preference.
- Click a Hermes session → `hermes://open/<session id>` opens it in Hermes Desktop. Claude Code / Copilot rows
  copy their resume command.

It is a port of the standalone *Agent Bar* app, fed by this plugin's own CLI instead of a separate server.

## Data source, privacy

The app starts no server and makes no network connection. It runs only the user's `hermes` CLI with fixed
arguments and a timeout, and `open` (through macOS) for `hermes://` links:

| Call | Default cadence |
| --- | --- |
| `hermes quota monitor snapshot --json` | every 30 s (Settings: 10–120 s) |
| `hermes quota status --json --cached` | every 60 s (30–300 s); reads the cache, never a provider |
| `hermes quota monitor history --json --days 1` | when a panel opens, at most every 5 min |
| `hermes quota monitor plan --json` | when a panel opens, at most every 10 min; skipped on plugins without `plan` |

Polling pauses while the displays sleep. Each call starts a Python process (about 1–2 s of CPU for the
snapshot on a typical Mac), so a shorter interval costs proportionally more CPU.

Session collection needs monitor enrollment. When it is off the app shows one line with the exact command:

```sh
hermes config set plugins.entries.quota.settings.monitorEnabled true
```

Account labels and session titles are displayed, never logged or stored. The only file the app writes is
`<hermes root>/quota_notch.json` (`version`, `notch`, `running_since`, `display`, `pid`), which
`hermes quota monitor companion --json` reads so the Desktop Settings card can say whether this Mac has a notch.

## Build, install, remove

Requires macOS 14+ and the Xcode Command Line Tools (`xcode-select --install`); no Xcode project or signing
identity.

```sh
macos/install.sh        # build (universal), install to ~/Applications/Quota Notch.app, start it
macos/uninstall.sh      # stop and remove it (--purge also forgets its settings and status file)
macos/build.sh          # build only → macos/build/Quota Notch.app
macos/test.sh           # Swift unit tests (synthetic fixtures, fake hermes)
macos/shots.sh <dir>    # screenshots on synthetic data with a separate bundle id
```

The plugin's own `install.sh` copies `macos/` as source only and never builds or starts the app. Launch at
login is a toggle in the app's Settings (macOS Login Items). The hermes path (auto-detected in
`~/.local/bin`, `~/.hermes/hermes-agent/.hermes/bin`, Homebrew and `PATH`) and the Hermes profile can be set
there too.

## Settings

Where it shows (notch + menu bar / notch only / menu bar only), language (system / English / Türkçe), rings
on hover or always, the right ear (sessions / today's cost / none), alerts and peeks with their durations, the
hover delay, what the hover panel shows, the menu bar label, notification banners, refresh intervals, the
hermes path and profile, launch at login.

## Not ported from Agent Bar

- **Desktop widget (WidgetKit)**: needs an Xcode build signed with a real developer identity.
- **Focusing a terminal tab / resuming in a new Terminal window**: needed AppleScript automation or running
  commands; this app only copies the resume command.
- **Agent Bar's other popover tabs** (model health and fallback chains, usage charts, top sessions, token-rate
  sparkline, Kanban, budgets): the Desktop widget already shows what the plugin collects; the companion keeps to
  quotas + sessions. Kanban, budget writes and credential-pool details are unavailable in the plugin's
  read-only monitor anyway.
- **panel.py-only data**: the per-account "This Mac's share" estimate, attributing a running session to one of
  several Claude accounts (`account` is always null in the plugin), per-model speed history beyond what
  `snapshot.health.speed` carries, quota refresh on demand (`/api/accounts?force=1`; the app reads the cache).
- **The web dashboard button** (there is no panel server any more).
- **Languages**: English and Turkish only (Agent Bar's set); the plugin's Desktop card ships all ten.
