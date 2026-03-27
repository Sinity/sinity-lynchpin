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
- **Snapshot manifests.** Raw export roots now carry a `MANIFEST.json` so
  snapshot boundaries can be audited and used for invalidation without
  modifying the source data.

## cachew wrapper (`lynchpin.core.cache`)
- Cache path: `artefacts/lynchpin/cache/<name>.sqlite`
- Invalidation: `file_signature` / `files_signature` (path + mtime + size) or
  `file_digest` / `files_digest` (content hash) depending on the source.
- Effect: when raw exports change, cachew invalidates and rebuilds on next call.

## Read-only sources (lazy, memoized where needed)

| Module | Canonical inputs | cachew | Notes |
| --- | --- | --- | --- |
| `lynchpin.sources.captures.activitywatch` | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | no | Live DB reads (no memoization). |
| `lynchpin.sources.captures.atuin` | `~/.local/share/atuin/history.db` | no | Live DB reads (no memoization). |
| `lynchpin.sources.exports.chatlog` | Polylogue Markdown (`/realm/data/exports/chatlog/processed/markdown/…`) | yes | Caches per provider + file signatures. |
| `lynchpin.sources.captures.codex` | Codex JSONL (`~/.codex/sessions`) | yes | Caches session list via file signatures. |
| `lynchpin.sources.libraries.dendron` | `/realm/project/knowledgebase` | no | Direct filesystem scan (YAML frontmatter). |
| `lynchpin.sources.exports.fbmessenger` | `/realm/data/exports/comms/facebook-messenger/processed/gdpr/<date>/messages/*.json` | yes | Cached JSON export parser (file signatures). |
| `lynchpin.sources.libraries.finance` | `/realm/data/libraries/finance/journal_clean`, statements | yes | Cached ledger parser (file signature). |
| `lynchpin.sources.exports.goodreads` | `/realm/data/exports/goodreads/raw/library_export.csv` | yes | Cached CSV parser (file signature). |
| `lynchpin.sources.indices.gitstats` | `/realm/project/*` repos | yes | Caches repo scans via file signatures. |
| `lynchpin.sources.exports.health` | `/realm/data/exports/health/raw/samsung-health/` | yes | Cached Samsung Health parser (CSV/tar signature). |
| `lynchpin.sources.captures.terminal_capture` | `/realm/data/captures/asciinema` | no | Reads raw terminal capture files; no cache yet. |
| `lynchpin.sources.captures.media_capture` | `/realm/data/captures/{audio/raw,screenshot}` | no | Reads raw audio/screen capture files; no cache yet. |
| `lynchpin.sources.exports.polylogue` | Polylogue Markdown transcripts + run metadata | yes | Caches inventories via file signatures. |
| `lynchpin.sources.exports.raindrop` | `/realm/data/exports/raindrop/raw/*.csv` | no | Parses CSV on demand. |
| `lynchpin.sources.exports.reddit` | `/realm/data/exports/reddit/processed/<date>/*.csv` | yes | Caches CSV parses via file signatures. |
| `lynchpin.sources.indices.repos` | `/realm/project/*` | no | Repo discovery/tokei scan on demand. |
| `lynchpin.sources.indices.sessions` | `artefacts/knowledge/ledgers/session_index.csv` | yes | Cached CSV reader (file signature). |
| `lynchpin.sources.exports.sleep` | `/realm/data/exports/health/processed/sleep_merged.jsonl` | yes | Cached JSONL parser (file signature). |
| `lynchpin.sources.exports.spotify` | `/realm/data/exports/spotify/processed/**/StreamingHistory*.json` | yes | Cached JSON parser (file signatures). |
| `lynchpin.sources.libraries.substack` | `/realm/data/libraries/substack` | no | Parses Markdown/HTML on demand. |
| `lynchpin.sources.exports.takeout_archives` | `/realm/data/exports/google/raw/takeout/*.tgz` | no | Reads Takeout archives on demand and expands multipart sets. |
| `lynchpin.sources.exports.takeout_life` | `/realm/data/exports/google/raw/takeout/*.tgz` | no | Builds the long-range life takeout bundle from archive readers. |
| `lynchpin.sources.exports.takeout_youtube` | `/realm/data/exports/google/raw/takeout/*.tgz` | no | Parses YouTube watch/search metadata and oEmbed caches. |
| `lynchpin.sources.captures.webhistory` | `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` | yes | Cached NDJSON scan (content hashes via `files_digest`). |
| `lynchpin.sources.captures.webhistory_raw` | `/realm/data/captures/webhistory/gestalt/raw` | yes | Cached raw exports (content hashes via `file_digest`). |
| `lynchpin.sources.exports.wykop` | `/realm/data/exports/wykop/raw/<user>/*.jsonl` | yes | Cached JSONL parser (file signatures). |

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
- `lynchpin.ingest.webhistory` → `/realm/data/captures/webhistory/gestalt/{data,derived}` (dedup segments + canonical full_history + reports).
- `lynchpin.ingest.fbmessenger_export` → `/realm/data/exports/comms/facebook-messenger/processed/fbmessengerexport.sqlite` (API-backed export).
- `lynchpin.ingest.wykop_export` → `/realm/data/exports/wykop/raw/<user>/` exports (network I/O).
- `lynchpin.context.reports` → `artefacts/context/reports/` (writes only when content changes).
- `lynchpin.retrospective.narrative` writes canonical markdown files under `artefacts/retrospective/narratives/YYYY/...`; rewrites preserve prior-version frontmatter instead of appending compatibility logs.
- `lynchpin.analysis.knowledge` → CSV ledgers under `artefacts/knowledge/ledgers/` via `just session-index` / `just artefact-index`.
- `lynchpin.views.knowledge_graph` → DuckDB snapshot + optional Parquet.
- `lynchpin.analysis.projects` → repomix-backed context bundles under
  `/realm/project/_context-project-bundles/` via `just project-bundles`.
- `lynchpin.analysis.projects` → `artefacts/meta/velocity/velocity.html` via `just velocity`.
- `lynchpin.views.warehouse` → DuckDB under `artefacts/lynchpin/warehouse.duckdb`.

## LLM outputs (non-deterministic)
- `lynchpin.analysis.knowledge` writes JSON summaries to
  `artefacts/knowledge/sessions/summaries/` and logs every call (backend/model,
  plus token/cost fields when available) to
  `artefacts/knowledge/sessions/logs/session_summaries.jsonl`.
- The command is **idempotent by default** (skips if output exists), but
  `--force` intentionally re-runs the model.
- `lynchpin.retrospective.narrative` is intentionally non-append-only: the canonical file for a period is rewritten in place, with prior passes preserved in frontmatter.

## Practical guidance
1. Use `lynchpin.sources.*` for reads; these are designed to be lazy and safe.
2. Use `lynchpin.views.*` / `lynchpin.ingest.*` only when you explicitly want
   regenerated artefacts/exports.
3. If a source module gets slow, add `persistent_cache` with a proper
   `depends_on` signature instead of creating ad-hoc derived files.
4. Wykop/Reddit caches are file-signature driven, so fresh exports automatically
   invalidate caches without mutating the raw datasets.
