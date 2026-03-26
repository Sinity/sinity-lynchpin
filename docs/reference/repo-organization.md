# sinity-lynchpin Repository Organization

This is the quick map for the current repo shape. The project is centered on the `lynchpin/` package plus a small set of operational docs and regenerated outputs.

## Top-Level Layout

- `lynchpin/`: canonical code surface.
- `docs/reference/`: current operational docs and maps.
- `docs/analysis/`: interpreted findings and narratives.
- `docs/personal/`: personal long-range materials and legacy narrative surfaces awaiting absorption into the unified system.
- `docs/plans/`: active near-term plans.
- `artefacts/`: regenerated output trees, ignored by Git.
- `config/`: HPI-style config for vendored `my.*` modules.
- `justfile`: lightweight repo helpers (`test`, `lint`, analysis dry-runs).

## Canonical Module Families

| Area | Modules | Notes |
| --- | --- | --- |
| Sources | `lynchpin.sources.*` | Lazy readers over canonical `/realm/data/...` and local app state. |
| Ingest | `lynchpin.ingest.*` | Small operational CLIs for source-specific refresh or metadata extraction. |
| Signals | `lynchpin.signals.*` | Shared activity-signal substrate: low-level event normalization, loaders, and attribution rules reused across ingest, processed views, and derived rollups. |
| Retrospective | `lynchpin.retrospective.*` | Narrative file I/O, enrichment, provenance, and authored retrospective outputs over shared evidence surfaces. |
| Context | `lynchpin.context.*` | Period-scoped evidence bundles, claims, and trust-aware orchestration surfaces for agents. |
| System workflows | `lynchpin.system.*` | Operational entrypoints for heavyweight artefact builds, validation, and baseline refreshes. Pull reusable logic down into `sources`, `context`, or `retrospective` rather than growing shell layers here. |
| Views | `lynchpin.views.*` | Materialized query surfaces such as warehouse and knowledge graph outputs. |
| Analysis | `lynchpin.analysis.*` | Optional code/project/knowledge analysis sidecar. Keep its helpers inside the analysis boundary instead of promoting them to generic top-level infrastructure. |

## Primary Commands

| Workflow | Command | Output |
| --- | --- | --- |
| Baseline | `python -m lynchpin.system.baseline` | `artefacts/core/baseline/latest/` |
| Interactive narratives | `lynchpin.context.build_period_evidence_bundle(...)`, `lynchpin.context.reports.build_period_reports(...)`, and `lynchpin-ops` / `lynchpin-narrative-improvement` workflows over warehouse/source/Polylogue evidence | `artefacts/retrospective/narratives/YYYY/...` plus colocated `.evidence/` bundles |
| Existing long-range artefacts | `lynchpin.retrospective.build_life_range(...)` or `python -m lynchpin.retrospective.life build --start ... --end ...` | `artefacts/retrospective/life-range/` |
| Period reports | `lynchpin.context.reports.build_period_reports(...)` or `python -m lynchpin.context.reports START END --scale day` | `artefacts/context/reports/` |
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
- `docs/reference/life-range.md`: current inherited long-range workflow to absorb, not the target architecture.
- `docs/reference/period-reports.md`: generic period-report workflow over evidence bundles.
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
