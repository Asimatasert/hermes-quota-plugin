# Claude accounts (multiple logins)

By default the `anthropic` provider shows the single login Hermes already uses.
To show additional Claude subscription logins side by side, list them explicitly
under the quota plugin's own settings. Nothing is discovered for you.

**Claude Code's setting (not invented here):** `CLAUDE_CONFIG_DIR` is a
documented Claude Code environment variable that relocates its whole config
directory. On Linux, Claude Code keeps the OAuth login in
`<CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json`, and Hermes's own reader
(`agent.anthropic_credentials`) reads that same file the same way. This plugin
reads it identically — but that layout is **implementation-derived** from Claude
Code's reader, not a published credential specification, and only the Linux form
is verified here (see the platform notes). The plugin only **reads** that file:
it never writes it, never refreshes it, and never moves a token between
directories.

**This plugin's opt-in setting:** `claudeAccounts`, a list under
`plugins.entries.quota.settings`. Each entry is `{id, label, configDir}`:

- `id` — required, stable and unique: 1–64 ASCII letters, digits, `.`, `_` or
  `-`, starting with a letter or digit. Becomes the cache key `anthropic:<id>`.
- `label` — optional display name (defaults to `id`), at most 80 characters,
  with no control characters. Unicode names are supported.
- `configDir` — required. The directory holding the `.credentials.json` written
  by Claude Code for **that** login.

```sh
hermes config set plugins.entries.quota.settings.claudeAccounts \
  '[{"id":"work","label":"Work","configDir":"~/.claude-work"},
    {"id":"personal","configDir":"~/claude/personal"}]'
```

The directories above are illustrative, portable and synthetic — substitute
your own. There is no repository- or plugin-defined naming standard for extra
Claude config directories, so the plugin assumes none: only the directories you
list are read.

**Plan names.** Each extra account's plan comes from `claudeAiOauth.subscriptionType` in its own `.credentials.json`, read locally. Only that one string is taken from the file; the access token is not read for this purpose. If the field is missing, the account shows no plan.

**Rate-limit cooldown.** After an HTTP 429 from the usage endpoint, the plugin stops reading that credential for 30 minutes. The state file stores a SHA-256 digest of the token, never the token. A corrupt or out-of-range state file fails open.

**Shared across profiles.** Hermes profiles usually share one Claude login, and the usage endpoint rate-limits per token, not per profile. The cooldown and the last successful usage payload are therefore kept in the Hermes root (not the active profile's home), keyed by the same token digest. A payload younger than 5 minutes is reused by every profile without a new request; while a 429 cooldown is active, the last good payload is shown for up to 60 minutes instead of an empty card. Payloads are never shared between different tokens, and a corrupt share file fails open.

Concurrent refreshes for the same token take an advisory file lock and
recheck the cache before requesting usage. Different tokens serialize only their
cache/cooldown updates, preserving one another's entries. Lock waiting and HTTP
use one request deadline; a contending waiter that exhausts it reports `timeout`
instead of issuing a duplicate request. The locks require a writable, trusted
Hermes root and a filesystem that supports advisory locking; unavailable storage
falls back to the historical uncached request behavior. Lock files contain no
credentials. The two shared-state locks are fixed files and stay; the per-token
request lock is removed by its holder on every exit path while still held, and
a waiter that wins an already-removed lock re-opens the path, so coordination
is never split. (Earlier releases kept one `quota_anthropic_shared.json.<digest>.lock`
per access token, i.e. one new file every time a token rotated; unheld ones
older than a day are pruned once per process.)

Reused data keeps its original observation time in an `Observed …` detail line,
including its age at refresh. Stale reuse is explicitly marked `stale; 429
cooldown` in `/quota`, the footer, widget tooltips, and even the clean-mode card.
The profile cache's top-level fetch time describes the refresh sweep, not a new
Anthropic observation. A non-429 failure never serves expired data. Both shared
state readers reject symlinks, FIFOs, devices, oversized files (over 1 MiB), and
invalid/non-finite/out-of-range timestamps. Native concurrent-process behavior
is verified on Linux; the Windows locking fallback is unit-tested, not verified
on a Windows installation. Unreadable state remains a cache miss for requests,
but an uncertain I/O failure (such as EIO or EACCES) aborts the persistent update
instead of replacing other accounts' state. Nonregular and oversized state is
also left untouched. Missing files may be initialized; demonstrably corrupt
JSON, invalid text encoding, or a non-object JSON root may be replaced.

`hermes config set` parses that JSON array into a real YAML list before it is
written (verified against the installed CLI), so no extra quoting is needed. If
another writer stores the value as a JSON *string* instead, the plugin parses it
the same way; a string that is not valid JSON is reported as
`config-invalid`.

The account list itself is edited through the config setting above; the Desktop
widget has no list editor. It **displays** each account as its own row and the
Settings cherry-picker can hide or show it like any other provider.

Rules the plugin holds to:

- **Opt-in only.** With no `claudeAccounts` — or an empty list, which behaves
  identically — nothing changes: one request, one `anthropic` card. No account is
  read unless it is listed; the plugin never scans `$HOME` and never guesses a
  directory.
- **Read-only; the core may refresh the primary.** The plugin reads an access
  token from the configured `.credentials.json` and sends it to the fixed
  Anthropic usage endpoint (`https://api.anthropic.com/api/oauth/usage`,
  redirects refused). It never exchanges or rewrites a refresh token and never
  writes to any Claude file. The *primary* card Hermes inherits is resolved by
  the core (`agent.anthropic_credentials`), which **may** refresh that inherited
  token itself; an extra account is never refreshed by this plugin, so once its
  access token expires the usage call returns `auth-failed` (401) until Claude
  Code next runs in that config directory and renews it externally.
- **Identity is the credential, never a directory.** The account Hermes already
  resolves (honouring `CLAUDE_CONFIG_DIR`) is the primary card. A listed entry is
  always read, even when it names the same directory as the primary — because the
  primary token may come from `ANTHROPIC_API_KEY`, the environment, or a
  different OAuth grant rather than that file. Dedup is exact: a listed directory
  named twice, or an access token byte-identical to the primary's or to another
  listed account's, collapses to a single card. Accounts are **never** merged by
  organization, by directory, or by an equal quota.
- **Per-account honesty.** Each account is its own row; one account failing
  (`no-credentials`, `auth-failed`, `timeout`, `config-invalid`, `config-limit`)
  never hides another's windows, and quota numbers are never summed. The
  failure's actionable detail is rendered in `/quota`, the footer and the widget
  tooltip — not just the reason code.
- **Bounded.** At most the first 8 entries are parsed and read, sharing one
  refresh budget; anything past the cap becomes a single `config-limit` row, so a
  config with many entries cannot flood the cache or the UI.
- **Safe reads.** A `configDir`'s `.credentials.json` is opened once and only a
  regular file of at most 64 KiB is read; a FIFO, a device, a symlink to either,
  or an oversized file is refused, so a hostile or accidental path cannot block
  the refresh thread or exhaust memory.

Platform notes — only what is proven:

- **Linux (verified):** a `.credentials.json` in the configured directory is read
  as `{"claudeAiOauth": {"accessToken": "…"}}` — the same file Hermes's own
  Claude reader reads.
- **Windows (unverified / experimental):** the `.credentials.json` layout is
  assumed for extra accounts but is **not** authoritatively verified here; a
  Windows Claude Code login may live in Windows Credential Manager or a
  DPAPI-protected store instead. Treat extra Windows accounts as experimental.
- **macOS:** Claude Code keeps its login in the Keychain (service
  `Claude Code-credentials`). Per-directory Keychain item naming is not
  documented, so the plugin does **not** read the Keychain for extra accounts and
  never asks you to export credentials by hand. A macOS extra account needs a
  directory that contains a `.credentials.json`; otherwise its row reads
  `unavailable (unsupported-platform)`. The default Hermes login is unaffected.
- **Invalid / excess entries** surface as `unavailable (config-invalid)` or
  `unavailable (config-limit)` with the exact fix in the detail line — shown in
  `/quota`, the footer and the widget tooltip. Nothing is silently ignored and no
  credential is read for a malformed entry.

Vendor account identity is not collected; a user-supplied label may itself identify a person: the cache carries only your `id`/`label`, the
computed windows and machine-readable reasons — never a token or a filesystem
path.


## Hermes pool accounts (`claudePoolAccounts`, opt-in)

Hermes keeps its own Claude logins in the `anthropic` credential pool
(`hermes auth add anthropic`, `hermes auth list anthropic`). With

```sh
hermes config set plugins.entries.quota.settings.claudePoolAccounts true
```

the Anthropic fetcher shows **one card per account behind that pool**, in pool
order, next to the primary card (default: off; only a real `true` opts in).
The grouping is ported from the original Agent Monitor:

- **Rows are read, never changed.** The rows come from Hermes' profile-aware
  reader (`hermes_cli.auth.read_credential_pool`; profiles without their own
  rows borrow the root pool, as Hermes does). Nothing is selected, rotated,
  written or refreshed; refresh tokens are not read.
- **Stored access token only, while unexpired.** A row whose `expires_at_ms`
  has passed (or passes within a minute) is never sent: it shows
  `token-expired · Hermes refreshes it on next use`.
- **One card per account.** Anthropic access tokens are opaque, so the account
  behind each token is asked once from Anthropic's OAuth profile endpoint
  (`GET https://api.anthropic.com/api/oauth/profile`, the call Claude Code
  itself makes, redirects refused). Rows of the same account — a Hermes PKCE
  login and the `claude_code` login, say — collapse into one card that is read
  with one token (the next one only if that read fails). A row whose account
  cannot be determined (e.g. HTTP 403 for a setup token without the
  `user:profile` scope) is never its own card: it is counted as *unverified*
  under the first card. If no row is readable at all, there is one plain card.
  `claudePoolIdentity: false` skips the profile call; rows are then grouped by
  exact token only.
- **Name.** The card is named after the account's first saved pool label in
  pool order, skipping the `claude_code` login (sanitized like Codex labels);
  a pill shows the pool position (`#1/3 · tried first` under `fill_first`).
- **Requests.** Profile calls: once per access token (a refreshed token is asked
  again), 401/403 not repeated for 30 minutes, other failures for 2 minutes, a
  429 opens that token's usage cooldown. Usage calls go through the same
  per-token shared payload (5 minutes), 30-minute 429 cooldown and request lock
  as every other Anthropic card, one account at a time inside the shared
  18-second multi-account budget.
- **What is stored.** The profile cache (`quota_cache.json`) holds, like the
  Codex and CommandCode pool cards, the display label, windows, pool
  position/size/strategy and credential counts. The shared state file
  `quota_anthropic_identity.json` in the Hermes root (0600, at most 256
  entries, 14-day retention) maps a SHA-256 digest of each token and of each
  pool-row id to a one-way account key (`sha256(account.uuid)`, 16 hex). Tokens,
  account ids, e-mail addresses, sources and paths are never stored.
- `claudeAccounts` entries whose token equals a pool row's token are not shown
  twice.

Upstream 3.0 deliberately left credential-pool metadata out of the hardened
**monitor**; this is a quota-provider read under the provider's own opt-in, and
the monitor still reads no pool metadata.

## Friendly names and icons

`label` is user-configurable; it is not a fixed ordinal or a translated product string.
For example, use `"label":"Work"` or `"label":"Account 2"`. If omitted, the stable `id` is displayed. The Desktop renders the base provider name plus the label (for example, **Anthropic · Work**) and the same provider logo as the primary account. A raw `anthropic:work` heading or missing card logo can indicate an older client widget. See [remote Desktop troubleshooting](troubleshooting.md#remote-desktop-old-names-or-missing-icons).

The list is currently edited through Hermes configuration, not a Desktop list editor.
