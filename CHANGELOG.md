# Changelog

Newest first. Each entry matches an annotated `vX.Y.Z` tag; the tag message is
the release note.

## 2.11.3 — 2026-10-23

- Cache: a carried record (incomplete sweep) now drops its windows and balances,
  so the CLI cannot render stale percentages under a fresh cache timestamp; the
  note is deduplicated instead of growing on every sweep.
- TUI: `rememberable` tolerates a non-array selection, and a test pins the
  persisted preference as the single string the picker resolves.

## 2.11.2 — 2026-10-23

- Cache: a sweep whose provider registry is incomplete no longer deletes the
  providers it never tried — they are carried forward and marked
  `not-refreshed`.
- Tests: the shared-cache refresh test patches its own cache path, so a test
  run can no longer rewrite the live `quota_cache.json` with a synthetic
  single-provider cache.
- Desktop: `WIDGET_VERSION` synced with the manifest (2.11.1 shipped a skew).

## 2.11.1 — 2026-10-22

- TUI: `/quota-select` opens on the provider/account saved by the previous run
  and marks that row `· current`; Enter keeps it instead of silently switching
  to the first cached row.
- TUI: a stale panel can no longer overwrite the saved preference with a
  selector the cache does not resolve.
- Tests: the picker test uses a temporary `HERMES_HOME`, so a test run never
  rewrites the live `quota-panel.state.json`.

## 2.11.0 — 2026-10-22

- `/quota` dashboard with friendly names, remaining-quota bars and worst-first
  ordering.
- Codex special-reset announcement on CLI, TUI and Desktop, with its public
  source and credit.
- Optional TUI quota chip and a saved dock preference that survives a restart.
