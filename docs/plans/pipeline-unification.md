# Pipeline Unification & Next-Gen Analysis Stack

Goal: collapse the current pipeline zoo into a cohesive “sinity-flow” stack that ingests once, normalizes into shared tables, and fans out derived artefacts (dashboards, narratives, ledgers, context bundles) without bespoke glue per outcome.

## 1. Current Coverage (today’s pipelines)

| Family | Entrypoints | Primary Inputs | Outputs / Consumers |
| --- | --- | --- | --- |
| Baseline rollups | `just baseline` | ActivityWatch, Atuin, Codex sessions, git, wearables (sleep) | `artefacts/core/baseline/latest/*`, provides timeline JSON to focus reports |
| Ledgers & knowledge graph | `just ledgers target=*`, `just knowledge-graph` | Docs (`docs/reference/sessions`, `artefact_catalog.json`, Markdown corpora) | CSV ledgers + DuckDB manifest powering lookup, polyglot agents |
| Focus dashboards | `just focus-portal`, `just calendar-refresh`, `just calendar-narrative` | Baseline timeline + ActivityWatch DB + Atuin | Calendar dossiers (Markdown/HTML/JSON), raw bundles, legacy portal mirror |
| Life logging | `just life-*`, `just youtube-oembed`, `just life-auto-narrative`, `just wykop-export` | Google Takeouts, Reddit/Wykop, finance, Samsung Health | Monthly JSON, drilldowns, narratives, digests, oEmbed cache |
| Instrumentation ingest | `just instrumentation target=*` | Asciinema/audio/screen raw capture dirs | Metadata JSONL for searches + future embeddings |
| Context bundles & meta | `just project-bundles`, `just velocity` | Git repos, artefact catalog | Repo-specific context packs, cross-project velocity dashboards |

Strengths:
- Domain coverage is broad; most longitudinal sources have at least one regeneration path.
- Outputs model “write to artefacts/” convention, so regenerations are safe.
- Just recipes exist for every pipeline, enabling scripted runs.

Pain points:
1. **Parallel silos** – each family touches raw sources directly with bespoke schemas; there is no shared warehouse, so every new analysis repeats parsing logic (ActivityWatch, Atuin, git, chat transcripts, etc.).
2. **No dependency graph** – assistants must remember to refresh baseline before focus dashboards, or life timeline before narratives; Just recipes don’t encode DAGs or versioned manifests.
3. **Limited incrementalism** – baseline offers `--since/--until`, but downstream consumers (dashboards, narratives) can’t request “delta since last run”; LEDGER outputs always overwrite entire CSVs.
4. **Insights remain document-centric** – narrative generation hinges on Markdown outputs instead of structured stores that Sinevec/Sinex could consume directly.
5. **Instrumentation & wearable streams are detached** – metadata JSONL exists, but nothing connects them to focus/baseline analytics, leaving multi-modal correlations manual.

## 2. Design Goals

1. **Single ingestion + normalization pass** per data source that materializes canonical tables (DuckDB + Parquet) under `artefacts/warehouse/`.
2. **Composable DAG** describing how downstream artefacts derive from normalized tables, so re-running `focus-portal`/`calendar-refresh` automatically ensures `baseline` + `warehouse` nodes are current.
3. **Time-sliced execution** – support `--range <start:end>` token that flows through the DAG, enabling day/week/month refreshes without editing scripts.
4. **Metadata manifest** capturing data lineage (inputs, schema version, run ID, git SHA, notes) to feed `docs/analysis-log.md` and future automated retrospectives.
5. **Unified metrics layer** – provide a small library (Python module + SQL view pack) that exposes standard measures (focus_minutes, afk_adjusted_span, command_density, git_net_loc, chat_tokens, sleep_quality, instrumentation_presence) so dashboards/narratives share definitions.
6. **Extensibility to Sinex** – treat every pipeline as a set of nodes in a future Sinex orchestrator (Rust or Python) so migration is incremental.

## 3. Proposed Architecture (“sinity-flow”)

```
          +-----------------+
          |   Orchestration |
          |  (just flow/... |
          |   + dag.json)   |
          +--------+--------+
                   |
          +--------v--------+
          |  Ingestion Hub  |
          | (connectors)    |
          +--------+--------+
                   |
          +--------v--------+
          | Warehouse Layer |
          | DuckDB/Parquet  |
          +--------+--------+
                   |
      +------------+-------------+
      |            |             |
+-----v----+ +-----v----+ +------v------+
| Semantics| | Dashboards| | Context Packs|
| (metrics | | & Reports | | & Ledgers    |
| lib/sql) | | (focus,   | | (sessions,   |
|          | | life, etc)| | bundles)     |
+----------+ +-----------+ +-------------+
```

### 3.1 Orchestration
- Introduce `pipelines/flow/` containing a declarative DAG (YAML/JSON) describing nodes, inputs, and outputs.
- Add `just flow [node=...] [range=...]` that resolves dependencies:
  - Example: `just flow node=focus.calendar range=2025-09-01:2025-10-01`.
  - Uses a thin Python orchestrator (or `doit`) to check modification times + manifest entries before running child scripts.
- Maintain run metadata in `artefacts/flow/runs/<timestamp>.json` capturing args, upstream nodes, success/failure, and output pointers.

### 3.2 Ingestion Hub
- Refactor existing connectors (ActivityWatch, Atuin, git, chat, wearable, instrumentation) into `pipelines/ingest/<source>/extract.py`.
- Each connector emits normalized tables into DuckDB via `pyarrow.dataset` writes:
  - `aw_windows`, `aw_afk`, `atuin_commands`, `git_commits`, `chat_sessions`, `wearable_sleep`, `wearable_activity`, `instrumentation_events`.
- Provide `pipelines/ingest/README.md` summarizing schema contracts and supported range filters.

### 3.3 Warehouse Layer
- Host DuckDB database at `artefacts/warehouse/sinity.duckdb` plus Parquet partitioned by `source=<name>/date=<YYYY-MM-DD>`.
- Provide SQL view pack under `pipelines/warehouse/views/*.sql` defining canonical joins (e.g., `focus_minutes_by_hour AS ...`).
- Use DuckDB macros to ingest new partitions; store schema version in `artefacts/warehouse/manifest.json`.

### 3.4 Semantics / Metrics Library
- New Python module `pipelines/lib/metrics.py` (or `pipelines/warehouse/metrics.py`) offering functions for commonly derived measures (AFK-adjusted spans, focus classification, git churn per window, chat token density, instrumentation pressure).
- Mirror definitions as SQL views to keep dashboards + scripts aligned.
- Provide tests (Pytest + DuckDB) verifying metric outputs given sample inputs.

### 3.5 Derived Artefacts
1. **Dashboards** (calendar dossiers, weekly rollups, narratives):
   - Refactor to read from the warehouse + metrics library rather than calling ActivityWatch HTTP or Atuin DB directly.
   - Accept `--range` parameter to filter views; caching handled at DuckDB level.
2. **Life timeline**:
   - Move monthly JSON generation to queries over normalized life events table (Takeout, finance, health, etc.).
   - Auto-link to instrumentation/AFK metrics for richer narratives.
3. **Context/knowledge outputs** (ledgers, project bundles, knowledge graph):
   - Connect to the manifest so each ledger row references the run ID that produced it.
   - Provide CLI flag `--manifest-run latest` to embed metadata in CSV headers.
4. **Artefact Catalog**:
   - Extend `pipelines/knowledge/ledgers/artefact_catalog.json` to include dependencies on warehouse tables; `just ledgers` verifies upstream run freshness before writing.

## 4. Migration Approach

### Phase 0 – Inventory & Schema Draft
1. Document schemas for every existing pipeline output (done partially above).
2. Define the canonical warehouse schema (column names/types, partitioning).
3. Add `pipelines/flow/README.md` describing orchestration goals.

### Phase 1 – Warehouse Bootstrap
1. Build ingestion connectors for ActivityWatch, Atuin, git, chat sessions, wearable sleep in isolation.
2. Create `just flow node=warehouse.bootstrap --range ...` to populate DuckDB + Parquet.
3. Write verification notebook (DuckDB SQL) ensuring counts match previous baseline outputs.

### Phase 2 – Dashboard Refactor
1. Update `pipelines/focus/calendar/*` scripts to query the warehouse instead of reading raw files per run.
2. Introduce `focus.metrics` module using shared metric functions.
3. Deprecate direct `ActivityWatch` CLI dependencies after parity validation.

### Phase 3 – Narrative & Knowledge Integration
1. Life timeline builder pulls from warehouse views; monthly JSON references run manifest IDs.
2. Session summaries log to the warehouse (table `session_summaries`) so Sinevec embeddings can operate directly on structured data.
3. Ledgers, project bundles, and knowledge graph consume the manifest to annotate provenance.

### Phase 4 – Instrumentation & Wearables Expansion
1. Add new connectors for audio/screencap metadata, heart rate, steps, stress metrics.
2. Extend metrics library with multi-modal correlations (e.g., `focus_block_has_asciinema` boolean, `avg_hr_during_focus`).

### Phase 5 – Sinex Integration
1. Expose the DAG + manifests via API/CLI so Sinex can orchestrate runs remotely.
2. Archive older standalone scripts once Sinex takes ownership; keep wrappers in `just` for backwards compatibility.

## 5. Immediate Actions

1. Create `pipelines/flow/` scaffold (README + sample DAG).
2. Define DuckDB schema + manifest spec under `pipelines/warehouse/`.
3. Pick one pipeline pair (baseline + calendar dossiers) as the first refactor target; measure parity via regression tests.
4. Update `docs/analysis-log.md` after each migration phase to track coverage and highlight newly unified metrics.
