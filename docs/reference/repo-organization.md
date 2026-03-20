# sinity-lynchpin Repository Organization

This is the quick map for the current repo shape. The project is centered on the `lynchpin/` package plus a small set of operational docs and regenerated outputs.

## Top-Level Layout

- `lynchpin/`: canonical code surface.
- `docs/reference/`: current operational docs and maps.
- `docs/analysis/`: interpreted findings and narratives.
- `docs/personal/`: high-sensitivity retrospectives.
- `docs/plans/`: active near-term plans.
- `artefacts/`: regenerated output trees, ignored by Git.
- `config/`: HPI-style config for vendored `my.*` modules.
- `justfile`: lightweight repo helpers (`test`, `lint`, analysis dry-runs).

## Canonical Module Families

| Area | Modules | Notes |
| --- | --- | --- |
| Sources | `lynchpin.sources.*` | Lazy readers over canonical `/realm/data/...` and local app state. |
| Ingest | `lynchpin.ingest.*` | Small operational CLIs for source-specific refresh or metadata extraction. |
| Retrospective | `lynchpin.retrospective.*` | Shared prompt builders, calendar/life assemblers, and renderers over typed trajectory/source summaries. |
| System workflows | `lynchpin.system.*` | Thin operational wrappers for heavyweight artefact builds plus baseline refreshes. |
| Views | `lynchpin.views.*` | Thin materializers that still earn dedicated modules: warehouse, calendar views, knowledge graph. |
| Analysis | `lynchpin.analysis.*` | Reusable project/code/knowledge analysis APIs. Prefer durable logic here over dedicated shells. |

## Primary Commands

| Workflow | Command | Output |
| --- | --- | --- |
| Baseline | `python -m lynchpin.system.baseline` | `artefacts/core/baseline/latest/` |
| Life timeline | `lynchpin.retrospective.run_life_timeline(...)` or `python -m lynchpin.system.life_timeline --start ... --end ...` | `artefacts/lifelog/life-timeline/` |
| Calendar views | `lynchpin.retrospective.build_calendar_views(...)` or `python -m lynchpin.views.calendar_views START END` | `artefacts/calendar/views/` |
| Range narratives | `lynchpin.retrospective.generate_date_range_narrative(...)` or the `lynchpin-ops` skill workflow | `artefacts/retrospective/narratives/logs/` |
| Warehouse views | `python -m lynchpin.views.warehouse build` | `artefacts/lynchpin/warehouse.duckdb` |
| Source materialization | `python -m lynchpin.views.warehouse materialize` or `refresh` | `artefacts/lynchpin/warehouse/{parquet,duckdb}/` |
| Ledgers | `python -m lynchpin.analysis.knowledge session-index` / `artefact-index`, the Python APIs, or `just session-index` / `just artefact-index` | `artefacts/knowledge/ledgers/` |
| Velocity | `python -m lynchpin.analysis.projects velocity`, the Python API, or `just velocity` | `artefacts/meta/velocity/velocity.html` |
| Session summaries | `python -m lynchpin.analysis.knowledge summarise-session --input ...`, the Python API, or `just summarise-session <conversation.md>` | `artefacts/knowledge/sessions/` |
| Instrumentation metadata | `python -m lynchpin.ingest.instrumentation ...` | `artefacts/ingest/instrumentation/` |
| Webhistory maintenance | `python -m lynchpin.ingest.webhistory ...` | `/realm/data/captures/webhistory/gestalt/...` plus `artefacts/webhistory/` |
| Wykop export | `python -m lynchpin.ingest.wykop_export ...` | `/realm/data/exports/wykop/raw/<user>/` |

## Key Reference Docs

- `docs/reference/data-sources.md`: canonical raw-input paths.
- `docs/reference/baseline.md`: direct baseline rebuild workflow.
- `docs/reference/life-timeline.md`: direct life-timeline composition workflow.
- `docs/reference/calendar-views.md`: day-view and narrative workflow.
- `docs/reference/hpi-modules.md`: curated supported vendored HPI surface.
- `docs/reference/webhistory.md`: canonical webhistory maintenance commands.
- `docs/reference/wykop-export.md`: Wykop export workflow.
- `docs/reference/velocity.md`: cross-repo velocity dashboard and reusable API.
- `docs/reference/warehouse.md`: warehouse table design and usage.
- `docs/reference/project-bundles.md`: project-context bundle materializer and reusable API.

## Extension Rules

1. Add new code under the relevant `lynchpin/<area>/` package, not a parallel top-level workflow tree.
2. Put operational usage docs in `docs/reference/` when a command becomes canonical.
3. Write regenerated outputs under `artefacts/` unless the canonical target is explicitly outside the repo.
4. Update `docs/reference/data-sources.md` and `docs/reference/ledgers/artefact_catalog.json` when new stable inputs or artefacts appear.
