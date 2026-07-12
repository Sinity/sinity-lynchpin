# Data sources

Lynchpin source modules are typed read APIs over owner-native data. Raw
captures and exports stay in their configured locations; source modules expose
availability, coverage, provenance, iterators, and source-local summaries.

## Roles

| Role | Examples | Contract |
| --- | --- | --- |
| Owner-native input | Application database, append-only capture, provider export, repository | Remains authoritative and is not rewritten by analysis. |
| Source API | `lynchpin.sources.*` | Parses lazily, preserves source caveats, and exposes typed values. |
| Canonical product | Derived NDJSON/manifest under the configured data root | Rebuildable normalization for formats that are expensive or ambiguous to query repeatedly. |
| Substrate table | `lynchpin.substrate.*` | Windowed DuckDB read model tied to a coherent `refresh_id`. |
| Analysis artifact | `lynchpin.analysis.*` output | Generated metric, map, diagnostic, or claim product with provenance. |
| Context pack | `lynchpin.graph.context_pack` | Bounded synthesis over graph/substrate evidence. |

## Source families

| Family | Representative modules | Evidence exposed |
| --- | --- | --- |
| Workstation activity | `activitywatch`, `terminal`, `clipboard`, `keylog`, `arbtt` | Focus spans, commands, sessions, recordings, input/activity events. |
| Code and delivery | `git`, `github`, `github_context`, `code_snapshots`, `xtask_history` | Commits, files, reviews, issues/PRs, snapshots, build/test history. |
| AI work | `polylogue`, `polylogue_timeline` | Session profiles, work events, costs, provider activity, timelines. |
| Machine state | `machine`, `machine_experiments`, `service_health`, `sinnix_generations` | Metrics, pressure, services, experiments, backups, generations. |
| Web and reading | `web`, `takeout_chrome`, `bookmarks`, `raindrop_live` | Visits, domains, bookmarks, content metadata, daily activity. |
| Communications | `communications`, `gmail_takeout`, `irc`, `outlook`, `sms`, export adapters | Events, threads, daily counts, provenance. |
| Health and daily signals | `health`, `sleep`, `personal_signals`, `weather` | Measurements, coverage-aware daily products, longitudinal signals. |
| Media and libraries | `spotify`, `spotify_genres`, `audio_features`, export adapters | Streams, sessions, library records, daily media signals. |
| Generated evidence | `analysis_artifacts`, `source_observations`, `observability_catalog` | Artifact inventory, extracted claims, source/role definitions. |

The exact filesystem roots come from `LynchpinConfig`. Tests use temporary
roots and neutral fixtures; the public source tree does not depend on one
operator's data layout.

## Invariants

- Missing coverage is not zero activity. Sources report observed bounds and
  whether they are continuous captures or bounded exports.
- Source-local normalization belongs in the source module; cross-source joins
  belong downstream.
- Cached values are invalidated by source signatures or explicit freshness
  contracts.
- Substrate rows and summaries are indexes, not replacements for raw logs.
- Generated analysis claims carry their artifact and refresh provenance.
- A legacy format leaves active discovery only after its canonical replacement
  is verified and the migration is complete.
