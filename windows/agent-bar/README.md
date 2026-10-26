# Agent Bar for Windows

A small "notch" pinned to the top centre of the primary monitor. It is the Windows counterpart of the macOS Agent Bar. Windows has no hardware notch, so the bar draws a black pill with rounded bottom corners that looks like one.

It is built only on what ships with Windows: **Windows PowerShell 5.1 + WPF**. There are no modules, no runtimes and no services. The scheduled task runs one `.ps1` file through a `.vbs` launcher, so no console window ever shows.

## What it shows

**Collapsed** (about 200×30): a coloured dot and a short summary, either `3 iş · %57 RAM` or `boşta`.

| Dot | Meaning |
|---|---|
| green | idle |
| blue, pulsing | agent jobs are running |
| orange | weekly Anthropic quota at 15 % or more |
| red | weekly quota at 20 % or more, **or** free RAM below 20 % |

**Expanded**: hover or click the pill. It grows into a 420 px card and collapses again 1.5 s after the mouse leaves. The card has four sections:

1. **Kota** (quota). Each Anthropic entry from `hermes quota status --json --cached` gets one ring for the current week and a smaller ring for the 5-hour session window, each with a reset countdown such as `2g 4s`. Ring colours: green below 50 %, yellow below 80 %, red from 80 %.
2. **Windows**:
   - RAM used/total and free %.
   - CPU %.
   - **Kapasite** (capacity): `running/max iş`, where `max = floor((freeGB − 0.20 × totalGB) / 3) + running`. This is the same formula as the macOS bar.
3. **İşler** (jobs): running agent sessions from `hermes quota monitor --json snapshot` (`live.running`), shown with status badges for working, approval needed, your turn, stalled or waiting. If the snapshot is unavailable, the section shows the number of `hermes` processes instead.
4. **WSL**: whether the `hermes-dev` distro is running, and its `docker ps` container count.
   - The bar first checks `wsl -l --running`, so it never starts a stopped distro.
   - The docker call must answer within 3 s, or it is shown as not responding.

Right-click the pill for a menu with **Yenile** (refresh now) and **Kapat** (quit). The interface text is in Turkish.

## Cost

Each Hermes CLI call starts Python. On the reference PC, `quota status --json --cached` costs about 4 CPU-seconds and `quota monitor --json snapshot` about 7. That is far more than the bar itself, so the bar avoids calls that cannot return anything new:

| What | How often |
|---|---|
| RAM / CPU (kernel32 `GlobalMemoryStatusEx`, `GetSystemTimes`) | every 10 s |
| `hermes quota status --json --cached` | checked every 30 s, but only run when `$HERMES_HOME\quota_cache.json` changed, and at least every 5 min |
| `hermes quota monitor --json snapshot` | every 30 s while the card is open, 60 s while jobs run, 120 s when idle; on hover, run at once if the last result is older than 15 s |
| `wsl -l --running` (+ `docker ps -q` when the distro runs) | every 30 s |
| `hermes quota refresh` | when the cache file is older than 15 min, at most once every 15 min |

At most one Hermes process runs at a time.

Every CLI call runs as an asynchronous child process with a timeout. The UI thread never waits on one, so a slow or missing Hermes CLI shows `veri yok` (no data) instead of freezing the bar.

The window is a transparent layered window. Transparent pixels pass clicks through to whatever is underneath, so only the pill or card itself takes the mouse. It has `WS_EX_TOOLWINDOW` (no taskbar button, not in Alt-Tab) and `WS_EX_NOACTIVATE` (never steals focus).

## Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The installer:
- copies `AgentBar.ps1` and `start-hidden.vbs` to `C:\Tools\HermesHost\AgentBar` (change the folder with `-Target`);
- registers the scheduled task `Hermes_AgentBar`, which runs at logon of the current user at run level **Limited** with no time limit;
- starts the task.

Options:
- `-NoStart`: register the task without starting it.
- `-Uninstall`: remove the task and stop the bar. The files are left in place.

Run by hand:

```powershell
powershell -NoProfile -STA -ExecutionPolicy Bypass -File AgentBar.ps1 [-Pinned] [-HermesPath C:\path\to\hermes.exe] [-WslDistro hermes-dev] [-WslUser dev]
```

- `-Pinned` keeps the card expanded, which is useful for screenshots.
- `hermes` is looked up on `PATH`, then in `%LOCALAPPDATA%\hermes\bin\hermes.exe`.
- Only one instance can run at a time (named mutex).
- Errors go to `agentbar.log` next to the script. The log is capped at 256 KB.

## Requirements

- Windows 10/11 with Windows PowerShell 5.1 (built in).
- The Hermes CLI with this quota plugin installed. Without it, the bar still runs and shows `veri yok`.
