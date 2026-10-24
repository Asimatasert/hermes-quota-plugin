# Agent Monitor in Hermes Quota 3.0

Agent Monitor adds live Hermes sessions, attention indicators, model health,
usage estimates, quota history and burn-rate forecasts inside Desktop. It uses
the gateway's `cli.exec` path, not a separate monitoring server. Monitor commands
read local data; quota fetching remains in the existing provider refresh path.

## Enrollment and scope

Collection ships **off by default**, independently of the existing quota view.
Run the supported command on the backend's machine for the intended profile:

```bash
hermes config set plugins.entries.quota.settings.monitorEnabled true
# Named profile:
hermes -p <profile> config set plugins.entries.quota.settings.monitorEnabled true
```

Set the same value to `false` to stop collection. This does not erase existing
history. Only the literal YAML boolean `true` counts as enrollment, not strings
or numbers. Without enrollment, monitor readers/store operations refuse access
with `monitor-enrollment-off`; the enrollment check itself reads configuration.

3.0 retains the read-only trial boundaries. The broader [proposal #83](https://github.com/rarf/hermes-quota-plugin/issues/83)
is not a list of capabilities all enabled in this release.

| Capability | Shipped behavior |
| --- | --- |
| Hermes sessions, messages, usage | Active profile only; SQLite read-only |
| Active session leases | Active profile runtime registry |
| Model health and fallback visibility | Local profile configuration and recorded activity/errors |
| History and forecasts | Existing quota cache plus monitor history; sufficient readings required |
| Context guard | Local estimates; `/compress` uses the Desktop composer path |
| Usage costs | Estimated API-price equivalents, not invoices or billed charges |
| Month-end spending projection | Unavailable; no projected amount rendered |
| Sibling profiles | Not collected (opt-in `monitorScope: all` adds their open sessions to Live) |
| External CLI transcripts (Claude Code, Copilot, etc.) | Collection disabled (opt-in `monitorScope: all`) |
| Shared Kanban boards | Collection disabled |
| New credential-pool metadata reads | Unavailable; monitor does not read auth.json or credential stores |
| Account priority/strategy and budget writes | Refused with `trial-read-only` |
| Monitor agent tools | Not registered; direct calls refuse with `trial-tools-disabled`. Exception: the separately opt-in, read-only `get_quota_plan` (`planToolEnabled`) |

Unavailable tabs do not enable these capabilities. Unavailable account metadata
is not evidence of an empty credential pool. Machine-readable `trial-*` codes
remain for compatibility with the evaluated candidate.

These boundaries apply to the **new monitor**. Existing quota providers retain
their own credential behavior and opt-ins; see [provider disclosures](providers.md)
and [security limitations](security.md). Python plugins are trusted host code,
not sandboxed applications.

## Commands

```bash
hermes quota monitor snapshot --json
hermes quota monitor history --json --days 7
hermes quota monitor recommend --json
hermes quota monitor budget --json
hermes quota monitor accounts --json
hermes quota monitor plan --json
```

Payloads carry `monitor_version` and `generated_at`. The monitor schema version
remains `1`, independently of the plugin release version. Snapshot sections fail
independently; inspect `errors` and `timings_ms` instead of interpreting missing
data as zero activity. Recommendations are advisory and do not switch models.
`set-budget`, `set-priority` and `set-strategy` are unavailable in this release.

## Scope: `monitorScope` (opt-in)

`plugins.entries.quota.settings.monitorScope` is `active` by default: the
hardened 3.0 boundary above. Setting it to the literal string `all` (and only
together with `monitorEnabled: true` in the same profile) restores what the
pre-3.0 Agent Monitor showed:

```bash
hermes config set plugins.entries.quota.settings.monitorScope all
```

- **Live view and agents chip** list the open sessions of **every Hermes
  profile** under this Hermes root (`<root>/state.db`, `<root>/profiles/*/state.db`,
  their `runtime/active_sessions.json` leases; SQLite read-only), grouped by
  profile.
- **Claude Code and Copilot CLI** sessions are collected again from this user's
  local files: `~/.claude/projects/**/*.jsonl` transcripts and
  `~/.claude/sessions` (Claude Code), `~/.copilot/session-store.db` and
  `~/.copilot/session-state` (Copilot CLI); the CLI-presence probe looks for the
  `claude`, `copilot`, `gemini` and `droid` binaries (and the `~/.gemini`, `~/.factory` folders). Session titles, models,
  token counts and working directories of those sessions then appear in Live and
  Usage. Nothing is written to those stores and nothing is sent anywhere.
- Kanban, credential-pool metadata in the monitor, account writes and the
  monitor agent tools stay unavailable in both scopes. The monitor database is
  still the active profile's.

Privacy: `all` shows content of sessions that belong to other profiles and to
other apps (titles, models, sizes, paths) inside this profile's Desktop pane.
Keep `active` on shared machines or when profiles separate people or clients.

The Live view and the agents card pin a **Now** block at the top (accent
stripe): the focused chat (`focusedStoredSessionId`) when it is open, and the
Anthropic account Hermes would use now (under `fill_first` the first account in
pool order whose windows are below 100 %; with `round_robin`/`random` the block
says requests are spread). The remaining sessions follow, grouped by source:
each Hermes profile, then Claude Code, then Copilot CLI.

## Plan: session vs week pacing

`hermes quota monitor plan --json` (also the **Plan** tab) answers, per account
with a short (5 h session) and a long (weekly) window — Anthropic
`Current session`/`Current week`, Codex `Session`/`Weekly`, CommandCode
`5h`/`Weekly` — how to spend the week across the sessions that are left. It is
pure: no network, no writes; it reads the quota cache and, when the monitor is
enrolled, up to 14 days of `quota_history` from `quota_monitor.db`.

Without enrollment the command still answers from the quota cache alone
(the same data the Quota tab shows), with `degraded: true`, the default ratio
and every 5 h window counted. No monitor store is opened.

### Formulas

| Quantity | Definition |
| --- | --- |
| `r` (`ratio.value`) | weekly % per 1 % of session. Per past session window (same week window, no reset inside, Δsession ≥ 10): Δweek/Δsession between its first and last reading; `r` = the Δsession-weighted median. Weekly % is reported as a whole percent, so short sessions are dominated by rounding; weighting lets long sessions decide. No samples: `r = 0.10`, confidence `low`. Confidence `high` with ≥ 3 samples and an inter-quartile range ≤ 0.6·r. |
| `typical_fill` | median of the highest session % in each finished session window |
| `pattern.hours` | per local hour (Hermes `timezone`, else the system zone): share of observed days on which the session % rose in that hour; ≥ 0.5 = active. Needs ≥ 2 observed days. `planActiveHours` (e.g. `"09-23"` or `["9-12","14-22"]`) overrides it. |
| `sessions.calendar` | running session (1 if its reset is in the future) + ⌈(week reset − session reset)/5 h⌉ (a final partial window counts) |
| `sessions.active` | windows the pattern would start: from the end of the running session, a window starts at the first active hour and occupies 5 h, until the week resets |
| `budget.week_per_session` | `week_left / sessions.active` |
| `budget.session_per_session` | `budget.week_per_session / r` (≥ 100 means every session can be used fully) |
| `current.target_used` | `min(100, session_used + budget.session_per_session)`; `can_fill` when it reaches 100; `state`: `keep_going`, `near_target` (≤ 5 points left), `over_target`, `unknown` |
| `projection.week_at_reset` | with history: `week_used + r·(max(0, fill − session_used) + fill·(sessions.active − 1))`; without: the week's straight-line rate so far. `exhaust_at` when it crosses 100 before the reset |
| `pace.verdict` | with history: expected use / week left `< 0.75` → `under` (`extra_session_pct` = budget − typical fill), `> 1.0` → `over` (`slow_to_session_pct` = budget), else `on_track`. Without: week used vs the even pace to reset, `over` above +5 points, `under` below −10 |
| `multi` | for ≥ 2 accounts of one provider (pool order): `use_index` (first account with session and week room), `headroom_index` (most weekly room per session), `advice` (`use_first`, `reorder`, `switch_now`, `spread` for non-fill-first strategies, `all_exhausted`), `switch` (session % at which to move on, and the next account), `combined_week_left` (sum of week left, one account = 100) |

`chart` carries the current week's `actual` readings, the `ideal` straight line
from week start (0 %) to reset (100 %) and the `projected` line. Every account
also gets an English `summary` sentence, e.g. *"6 sessions left until Fri 13:00;
you can use ~6.67% of the week per session (~55% of a session). Current
session: 20% used - keep going."* All of it is an estimate from readings, not a
provider guarantee; sessions start with the first message after the previous
one expired, so the calendar count is an upper bound.

Payload: `plan_version` (1), `monitor_version`, `generated_at`, `enrolled`,
`degraded`, `history_readings`, `timezone`, `fetched_at`,
`settings.active_hours`, `defaults`, `providers[] {base, accounts[], multi}`,
`errors`, `timings_ms`. Unplannable accounts carry `plannable: false` and a
`reason` (`no-window-pair`, `missing-values`, `week-reset-passed`, or the
card's unavailable reason such as `token-expired`).

### Agent tool `get_quota_plan` (opt-in)

```bash
hermes config set plugins.entries.quota.settings.planToolEnabled true
```

Only the literal boolean `true` registers one read-only tool,
`get_quota_plan` (toolset `quota_monitor`), at plugin load; the handler
re-checks the setting on every call and answers `plan-tool-disabled` once it
is off. It returns numbers and enum codes only: accounts are `account: 1..N` in
pool order, with no labels, ids, titles, paths or provider error text (free-text
reasons collapse to `unavailable`). Use it to self-pace, e.g. prefer a cheaper
model while `pace` is `over`. `plugin.yaml` keeps `provides_tools: []` because
the manifest has no conditional form and `hermes plugins doctor` runs without
the setting; `hermes plugins list` therefore does not name the tool. Restart
Hermes after changing the setting.

Other agents: Hermes' MCP server is the separate official `mcp-serve` plugin
(`hermes plugins install mcp-serve`, then `hermes mcp-serve --tools`); its
`--tools` mode exposes a fixed allowlist (web, browser, vision, image, skills,
TTS) and **does not expose plugin tools**, so `get_quota_plan` is not reachable
through it. For Claude Code or any other agent with a shell, call the CLI
instead: `hermes quota monitor plan --json` (same data, with labels; filter it
yourself if the agent must not see them).

## Storage and estimates

The monitor records its own history and sampling data in the active profile's
`quota_monitor.db`. The database is private (`0600`); creation uses a private
parent, rejects database/sidecar symlinks, and uses WAL with a busy timeout.
Read paths do not create the database or run DDL. This is filesystem hardening,
not isolation from other trusted code running as the same OS user.

Quota history comes from existing cache readings. Identical consecutive readings
are deduplicated, with periodic retention. History is pruned after 35 days and
rate samples after one hour. Forecasts estimate a trend from recent readings;
resets, sparse data and changing usage can make them unavailable or inaccurate.
They are not provider guarantees.

Context uses local usage anchors and model metadata. Transcript-length estimates
are approximate; unknown model limits stay unknown without a network probe.

## Desktop and remote gateways

The widget runs on the Desktop computer; the backend runs on the selected
gateway/profile. Update both halves separately. Installing a Mac widget does
not update a Linux backend. Replacing backend files does not prove a running
gateway reloaded them; inspect version warnings and fresh runtime output.

The localized monitor UI includes English, Turkish, German, Spanish, French,
Russian, Japanese, Simplified Chinese, Traditional Chinese and Arabic (RTL).
Native notifications depend on SDK support, OS permission and app settings.
Missing platform capabilities degrade to unavailable/empty values. The original
standalone app's native menu-bar/notch widgets are not included.

## Attribution

Thank you to [@Asimatasert](https://github.com/Asimatasert) for the
[agent-monitor](https://github.com/Asimatasert/agent-monitor) contribution and
follow-up fixes. See [Acknowledgements](contributors.md).
