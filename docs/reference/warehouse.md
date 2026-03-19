# Lynchpin Warehouse

The warehouse is a query surface over canonical inputs and derived
understanding layers. Raw exports stay under `/realm/data/...`; the warehouse
exists so agents can inspect the current typed contracts in one place.

## Layout
- Per-source outputs (materialized tables):
  - Parquet: `artefacts/lynchpin/warehouse/parquet/<source>/<table>.parquet`
  - DuckDB (optional): `artefacts/lynchpin/warehouse/duckdb/<source>.duckdb`
- View database: `artefacts/lynchpin/warehouse.duckdb` (views over per-source
  outputs).
- Code layout:
  - public module/CLI: `lynchpin.views.warehouse`
  - table/source specs: `lynchpin/views/_warehouse/specs.py`
  - row extraction: `lynchpin/views/_warehouse/rows_{sources,trajectory,analysis}.py`
  - materialization/build/CLI helpers: `lynchpin/views/_warehouse/ops.py`

`parquet` is the recommended default for portable views. DuckDB per-source outputs are still supported, but view definitions that use `ATTACH` require re-attaching on each new connection.

## Commands
- Build views only: `python -m lynchpin.views.warehouse build`
- Materialize per-source outputs: `python -m lynchpin.views.warehouse materialize --format parquet`
- Refresh both outputs and views: `python -m lynchpin.views.warehouse refresh --format parquet`

Config overrides:
- `LYNCHPIN_WAREHOUSE_ROOT` for the per-source output root.
- `LYNCHPIN_WAREHOUSE_DB` for the view database path.

## Inclusion rules
The warehouse should cover every `lynchpin.sources.*` or derived read-model
surface that yields stable, queryable rows.

Include:
- structured source event streams,
- metadata inventories and quality ledgers,
- typed trajectory derivations,
- session and transcript semantics that have been promoted into stable rows.

Exclude:
- raw exports,
- large binaries such as audio/video/screens,
- ephemeral caches that are not part of the inspectable contract.

If a module exists and can be expressed as a stable table, it should have a
table spec in `lynchpin/views/_warehouse/specs.py`.

## Tables

### Source tables
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
- instrumentation: `instrumentation_terminal_sessions`, `instrumentation_terminal_events`, `instrumentation_audio`, `instrumentation_screen`
- raindrop: `raindrop_bookmarks`
- reddit: `reddit_comments`, `reddit_posts`, `reddit_message_headers`, `reddit_saved`, `reddit_votes`
- sleep: `sleep_entries`, `sleep_segments`
- spotify: `spotify_streams`
- substack: `substack_posts`
- takeout: `takeout_archives`
- webhistory: `webhistory_entries`
- webhistory_raw: `webhistory_raw_entries`
- wykop: `wykop_entries`, `wykop_entry_comments`, `wykop_link_comments`

### Trajectory read-model tables
- `trajectory_signal`
- `trajectory_chain`
- `trajectory_chain_topic`
- `trajectory_day`
- `trajectory_day_project`
- `trajectory_day_topic`
- `trajectory_day_event`
- `trajectory_signal_coverage`
- `trajectory_period`
- `trajectory_week`
- `trajectory_month`
- `trajectory_quarter`
- `trajectory_year`
- `trajectory_period_project`
- `trajectory_period_topic`
- `trajectory_episode`
- `trajectory_anomaly`

These tables encode lynchpin's current understanding of the inputs. They are
useful reference contracts for future Sinex work, not a promise that lynchpin
itself is the long-term runtime.

### Polylogue and session semantics
- `polylogue_markdown`
- `polylogue_runs`
- `polylogue_session_profile`
- `polylogue_work_event`
- `polylogue_work_thread`
- `polylogue_session_tag`
- `sessions_records`
- `session_summaries`

`session_summaries` is loaded from
`artefacts/knowledge/sessions/summaries/*.json` and exposes generated Level-1
summary payloads as stable warehouse rows alongside the session ledger.

### Other derived tables
- `narrative`
- `warehouse_manifest`

Table inventory and coverage are recorded in `warehouse_manifest` inside the view DB.

## Validation vs warehouse
Validation checks that sources are discoverable and minimally parseable. The
warehouse materializes queryable contracts for inspection and downstream use.
Validation is lightweight and can run frequently; warehouse refreshes are
heavier and only needed when inputs change or when a new derived table lands.
