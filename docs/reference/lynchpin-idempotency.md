# Lynchpin Laziness + Idempotency Review

This report captures how the Lynchpin API behaves with respect to laziness,
memoization, and write side-effects. It is meant to keep the HPI-style contract
clear as modules grow.

## Core contract
- **Parse late.** Canonical exports stay untouched under `/realm/data/...`; all
  parsing happens at access time through `lynchpin.sources`.
- **Cache, don’t normalize.** `lynchpin.core.cache.persistent_cache` uses
  `cachew` to memoize expensive reads into `artefacts/lynchpin/cache/*.sqlite`.
- **Explicit writes only.** Anything under `lynchpin.views` or `lynchpin.ingest`
  writes artefacts/exports and should only run via explicit CLI/`just` calls.

## cachew wrapper (`lynchpin.core.cache`)
- Cache path: `artefacts/lynchpin/cache/<name>.sqlite`
- Invalidation: `file_signature` / `files_signature` (path + mtime + size) or
  `file_digest` / `files_digest` (content hash) depending on the source.
- Effect: when raw exports change, cachew invalidates and rebuilds on next call.

## Read-only sources (lazy, memoized where needed)

| Module | Canonical inputs | cachew | Notes |
| --- | --- | --- | --- |
| `lynchpin.sources.activitywatch` | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | no | Live DB reads (no memoization). |
| `lynchpin.sources.atuin` | `~/.local/share/atuin/history.db` | no | Live DB reads (no memoization). |
| `lynchpin.sources.chatlog` | Polylogue Markdown (`/realm/data/chatlog/markdown/…`) | yes | Caches per provider + file signatures. |
| `lynchpin.sources.codex` | Codex JSONL (`~/.codex/sessions` or `/realm/data/chatlog/codex_sessions`) | yes | Caches session list via file signatures. |
| `lynchpin.sources.dendron` | `/realm/project/knowledgebase` | no | Direct filesystem scan (YAML frontmatter). |
| `lynchpin.sources.finance` | `/realm/data/finance/journal_clean`, statements | yes | Cached ledger parser (file signature). |
| `lynchpin.sources.goodreads` | `/realm/data/goodreads/library_export.csv` | yes | Cached CSV parser (file signature). |
| `lynchpin.sources.gitstats` | `/realm/project/*` repos | yes | Caches repo scans via file signatures. |
| `lynchpin.sources.health` | `/realm/data/health/raw/samsunghealth.tar` | yes | Cached Samsung Health parser (tar signature). |
| `lynchpin.sources.instrumentation` | `/realm/data/{asciinema_recording,audio/raw,screenshot}` | no | Reads raw capture files; no cache yet. |
| `lynchpin.sources.polylogue` | Polylogue Markdown transcripts | yes | Caches inventory + metadata via file signatures. |
| `lynchpin.sources.raindrop` | `/realm/data/raindrop/*.csv` | no | Parses CSV on demand. |
| `lynchpin.sources.reddit` | `/realm/data/reddit/gdpr/<date>/*.csv` | yes | Caches CSV parses via file signatures. |
| `lynchpin.sources.repos` | `/realm/project/*` | no | Repo discovery/tokei scan on demand. |
| `lynchpin.sources.sessions` | `artefacts/knowledge/ledgers/session_index.csv` | yes | Cached CSV reader (file signature). |
| `lynchpin.sources.sleep` | `/realm/data/health/processed/sleep_merged.jsonl` | yes | Cached JSONL parser (file signature). |
| `lynchpin.sources.spotify` | `/realm/data/spotify/gdpr/**/StreamingHistory*.json` | yes | Cached JSON parser (file signatures). |
| `lynchpin.sources.substack` | `/realm/data/doc/substack` | no | Parses Markdown/HTML on demand. |
| `lynchpin.sources.takeout` | `/realm/data/google/takeout/raw/*.tgz` | no | Reads Takeout archives on demand. |
| `lynchpin.sources.webhistory` | `/realm/data/webhistory/gestalt/data` (or `/realm/data/webhistory/gestalt/derived/full_history.ndjson`) | yes | Cached gestalt/NDJSON scan (content hashes via `files_digest`). |
| `lynchpin.sources.webhistory_raw` | `/realm/data/webhistory/gestalt/raw` | yes | Cached raw exports (content hashes via `file_digest`). |
| `lynchpin.sources.wykop` | `/realm/data/wykop/<user>/*.jsonl` | yes | Cached JSONL parser (file signatures). |

Notes:
- The **non-cached** modules are still deterministic and read-only; they are
  simply fast enough that cachew wasn’t added yet.
- DB-backed sources (ActivityWatch, Atuin) are *idempotent* but not memoized,
  because the DBs update continuously and are small enough to query directly.

## Writers (explicit side-effects)
These modules write artefacts/exports and should never run implicitly from
`lynchpin.sources` calls:
- `lynchpin.ingest.instrumentation` → JSONL metadata under
  `artefacts/ingest/instrumentation/`.
- `lynchpin.ingest.webhistory` → `/realm/data/webhistory/gestalt/{data,derived}` (canonical segments + full_history + dedup reports).
- `lynchpin.ingest.wykop_export` → `/realm/data/wykop/<user>/` exports (network I/O).
- `lynchpin.views.calendar_views` → `artefacts/calendar/views/` (writes only when content changes).
- `lynchpin.views.calendar_narratives` → `artefacts/calendar/narratives/**` (skips if output exists unless `--force`; prompt/output writes are change-aware).
- `lynchpin.views.ledgers` → CSV ledgers under `artefacts/knowledge/ledgers/`.
- `lynchpin.views.knowledge_graph` → DuckDB snapshot + optional Parquet.
- `lynchpin.views.project_bundles` → context bundles under
  `artefacts/context/project-bundles/`.
- `lynchpin.views.velocity` → `artefacts/meta/velocity/velocity.html`.
- `lynchpin.views.warehouse` → DuckDB under `artefacts/lynchpin/warehouse.duckdb`.

## LLM outputs (non-deterministic)
- `lynchpin.views.session_summaries` writes JSON summaries to
  `artefacts/knowledge/sessions/summaries/` and logs every call (tokens + cost)
  to `artefacts/knowledge/sessions/logs/session_summaries.jsonl`.
- The command is **idempotent by default** (skips if output exists), but
  `--force` intentionally re-runs the model.
- `lynchpin.views.calendar_narratives` logs every successful run to
  `artefacts/calendar/narratives/logs/narrative_runs.jsonl` (model + timing +
  token usage/cost when `codex prompt --verbose` reports it).

## Practical guidance
1. Use `lynchpin.sources.*` for reads; these are designed to be lazy and safe.
2. Use `lynchpin.views.*` / `lynchpin.ingest.*` only when you explicitly want
   regenerated artefacts/exports.
3. If a source module gets slow, add `persistent_cache` with a proper
   `depends_on` signature instead of creating ad-hoc derived files.
4. Wykop/Reddit caches are file-signature driven, so fresh exports automatically
   invalidate caches without mutating the raw datasets.
