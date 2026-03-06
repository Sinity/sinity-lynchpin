# Lynchpin Warehouse

The warehouse is a query surface, not a primary store. Raw exports stay under `/realm/data/...`. The warehouse builds per-source tables and a view-only DuckDB that points at them so downstream tools can query everything with a single SQL connection.

## Layout
- Per-source outputs (materialized tables):
  - Parquet: `artefacts/lynchpin/warehouse/parquet/<source>/<table>.parquet`
  - DuckDB (optional): `artefacts/lynchpin/warehouse/duckdb/<source>.duckdb`
- View database: `artefacts/lynchpin/warehouse.duckdb` (views over per-source outputs).

`parquet` is the recommended default for portable views. DuckDB per-source outputs are still supported, but view definitions that use `ATTACH` require re-attaching on each new connection.

## Commands
- Build views only (fast): `just lynchpin-warehouse`
- Materialize per-source outputs: `just lynchpin-warehouse mode=materialize format=parquet`
- Refresh both outputs + views: `just lynchpin-warehouse mode=refresh format=parquet`
- One-shot bundle: `just materialize` (runs webhistory, ledgers, warehouse, optional velocity/baseline)

Config overrides:
- `LYNCHPIN_WAREHOUSE_ROOT` for the per-source output root.
- `LYNCHPIN_WAREHOUSE_DB` for the view database path.

## Inclusion rules
The warehouse should cover every `lynchpin.sources.*` module that yields structured, queryable rows.
- Include: structured event streams, indexes, metadata inventories, numeric/temporal series.
- Exclude: raw exports, large binaries (audio/video/screens), and ephemeral caches. Keep those in `/realm/data/...` and expose paths in the warehouse when helpful.

If a module exists and can be expressed as a stable table, it should have a table spec in `lynchpin/views/warehouse.py`.

## Tables (current)
- activitywatch: `activitywatch_window`, `activitywatch_afk`, `activitywatch_web`
- atuin: `atuin_commands`
- chatlog: `chatlog_transcripts`
- codex: `codex_sessions`
- dendron: `dendron_notes`
- finance: `finance_transactions`
- fbmessenger: `fbmessenger_threads`, `fbmessenger_messages`
- gitstats: `gitstats_commits`
- goodreads: `goodreads_library`
- health: `health_samsung_sleep`, `health_samsung_weight`
- instrumentation: `instrumentation_asciinema`, `instrumentation_audio`, `instrumentation_screen`
- polylogue: `polylogue_markdown`, `polylogue_runs`
- raindrop: `raindrop_bookmarks`
- reddit: `reddit_comments`, `reddit_posts`, `reddit_message_headers`, `reddit_saved`, `reddit_votes`
- sessions: `sessions_records`
- sleep: `sleep_entries`, `sleep_segments`
- spotify: `spotify_streams`
- substack: `substack_posts`
- takeout: `takeout_archives`
- webhistory: `webhistory_entries`
- webhistory_raw: `webhistory_raw_entries`
- wykop: `wykop_entries`, `wykop_entry_comments`, `wykop_link_comments`

Table inventory and coverage are recorded in `warehouse_manifest` inside the view DB.

## Validation vs warehouse
Validation checks that sources are discoverable and minimally parseable. The warehouse materializes data for queries. Validation is lightweight and can run frequently; warehouse refreshes are heavier and only needed when inputs change or a new analysis needs cached tables.
