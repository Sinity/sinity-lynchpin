# Data Sources

Lynchpin source modules are the canonical read APIs over raw local data. The
shape is intentionally simple: raw exports and captures stay in their owning
locations, source modules expose typed/lazy iterators, and substrate/analysis
products are derived read models.

## Roles

| Role | Owner | Examples | Lynchpin contract |
| --- | --- | --- | --- |
| Raw capture/export | External service, Sinnix, or provider export | ActivityWatch DB, Atuin DB, Samsung exports, machine telemetry SQLite | Read in place; do not rewrite as part of analysis. |
| Source API | `lynchpin.sources.*` | `activitywatch`, `terminal`, `polylogue`, `machine`, `health` | Typed Python access with provenance, caveats, and lazy parsing. |
| Substrate table | `lynchpin.substrate.*` | `commit_fact`, `ai_work_event`, `machine_metric_sample` | Derived, rebuildable DuckDB read model with `refresh_id`. |
| Analysis artifact | `lynchpin.analysis.*` | `project_velocity_windows.json`, `machine_telemetry_analysis.json` | Generated summaries under `.lynchpin/generated/analysis/`. |
| Context pack | `lynchpin.graph.context_pack` | current-state packs and narratives | LLM-facing synthesis over graph/source evidence, not raw truth. |

## Active Source Modules

| Module | Primary input | Main output shape |
| --- | --- | --- |
| `lynchpin.sources.activitywatch` | ActivityWatch SQLite | events, focus spans, sessions, deep-work blocks, daily activity |
| `lynchpin.sources.terminal` | Atuin SQLite and asciinema captures | shell commands, shell sessions, terminal recordings |
| `lynchpin.sources.git` | Local repos and baseline JSONL | commits, file changes, sessions, repo metadata |
| `lynchpin.sources.polylogue` | Polylogue archive DB / chatlog exports | session profiles, daily chat activity, work patterns, cost summaries |
| `lynchpin.sources.machine` | `/realm/data/captures/machine/telemetry.sqlite` | metric, GPU, service, and network samples |
| `lynchpin.sources.machine_experiments` | `/realm/data/captures/machine/experiments` | benchmark/workload manifests |
| `lynchpin.sources.health` | processed Samsung Health exports | daily health, stress, HR, HRV, SpO2, weight, movement |
| `lynchpin.sources.sleep` | processed sleep exports | sleep entries and sleep/productivity joins |
| `lynchpin.sources.substance` | processed substance CSV | dose entries and daily/monthly summaries |
| `lynchpin.sources.web` | browser history captures/exports | visits, daily browsing, domain breakdowns |
| `lynchpin.sources.takeout_chrome` | Google Takeout Chrome JSON | normalized web history visits |
| `lynchpin.sources.google_takeout_products` | canonical Google Takeout product NDJSON | contacts, Keep notes, My Activity, purchases, Play Store, tasks, YouTube rows, asset inventory |
| `lynchpin.sources.spotify` | Spotify processed exports | streams, listening sessions, daily listening |
| `lynchpin.sources.reddit` | Reddit processed export | posts, comments, votes, daily activity |
| `lynchpin.sources.exports` | Goodreads, Raindrop, Messenger, Wykop, notes exports | per-export iterators and daily summaries |
| `lynchpin.sources.analysis_artifacts` | `.lynchpin/generated/analysis` | generated artifact inventory and extracted claims |
| `lynchpin.sources.observability_catalog` | code-defined operational catalog | machine/performance observability input roles |

## Dataflow Invariants

- Source modules read raw data or owner-native ledgers; they do not become a
  second warehouse.
- Substrate tables are rebuildable indexes. Schema changes may reset DuckDB;
  raw captures and source modules remain authoritative.
- Generated analysis artifacts are evidence products. They are inventoried by
  `lynchpin.sources.analysis_artifacts` and can become graph nodes, but they do
  not override source-level facts.
- Legacy/intermediate formats should leave active namespaces after successful
  one-shot backfill. Quarantined raw artifacts belong under `/realm/inbox`.
- Machine/process troubleshooting preserves dimensions: machine metrics,
  service state, experiment manifests, and `below` process/cgroup windows are
  separate joined surfaces, not one collapsed scalar.
