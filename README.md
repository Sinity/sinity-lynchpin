# Lynchpin

Lynchpin is a local-first evidence and analysis platform for personal data
exports, activity captures, code history, AI-session archives, and machine
telemetry. It turns heterogeneous records into typed source APIs, reproducible
materialized products, a DuckDB evidence substrate, cross-source graphs, and
bounded context packs for analysis and agent use.

The project is designed for a single operator with a long-running local data
lake. It is not a hosted service and it does not put private data in Git. The
repository contains the reusable parsers, schemas, analyses, query tools, and
neutral fixtures; identities, vocabularies, private classifications, raw
exports, and generated personal narratives are supplied externally.

## Why it exists

Most personal evidence is individually legible but collectively difficult to
use. A Git commit, terminal command, focus interval, health measurement,
machine-pressure sample, and AI work event may describe the same hour while
living in unrelated databases and export formats.

Lynchpin preserves those sources and adds the joins:

```text
captures / exports / repositories / service ledgers
                       │
                       ▼
              typed source adapters
                       │
                       ▼
        canonical materialized products + manifests
                       │
                       ▼
              DuckDB evidence substrate
                 │              │
                 ▼              ▼
          evidence graph    analysis products
                 │              │
                 └──────┬───────┘
                        ▼
            context packs, CLI, and MCP
```

Raw data remains in its owning system. Materialized datasets and the substrate
are rebuildable read models with coverage, freshness, and provenance attached.
Narrative output is downstream of evidence rather than a replacement for it.

## What Lynchpin provides

### Source adapters

`lynchpin.sources` exposes lazy, typed access to sources including:

- ActivityWatch focus and application events;
- Atuin commands and asciinema terminal recordings;
- Git repositories, GitHub context, code snapshots, and review history;
- Polylogue session profiles and work events;
- browser history, bookmarks, clipboard, and communication exports;
- wearable health, sleep, media, and other provider exports;
- machine metrics, service state, benchmark manifests, and system generations.

Adapters preserve source-specific caveats and coverage. Missing observations
are not silently converted to zero, and export-bounded datasets remain
distinguishable from continuous captures.

### Materialization and substrate

The materialization planner determines which canonical products are missing or
stale, rebuilds them in dependency order, records their manifests, and can
promote a coherent time window into DuckDB. Refresh IDs make graph and analysis
products traceable to one substrate snapshot.

The substrate supports ordinary SQL as well as stable readers for commits,
files, symbols, work events, personal signals, machine state, evidence claims,
and graph edges. It is an acceleration and join layer, not a second raw-data
archive.

### Evidence graphs and context packs

The graph layer connects projects, commits, files, AI work events, terminal
activity, focus spans, GitHub items, analysis claims, personal signals, and
machine context. It supports:

- project/day and session/commit correlation;
- issue → pull request → commit closure chains;
- file- and symbol-overlap evidence;
- source readiness and confidence matrices;
- chronological current-state timelines;
- bounded Markdown or JSON context packs with explicit weak-evidence options.

### Analysis

The analysis package covers both the codebase and the operator's evidence:

- code velocity, change surfaces, dependency maps, refactor candidates, and
  cross-project comparison;
- work rhythms, focus fragmentation, AI-session efficiency, and workflow
  mechanics;
- health, sleep, mood, and other longitudinal signal analyses;
- machine pressure, workload co-presence, service behavior, experiments, and
  calibrated attribution claims;
- analysis-artifact inventories and claim/evidence promotion.

Canonical metrics are deterministic and carry a timeframe, unit/denominator,
and artifact path. Interpretive products must retain those boundaries.

### A compact MCP contract

The MCP server presents eight stable public tools rather than exposing every
internal function directly:

| Tool | Domain |
| --- | --- |
| `lynchpin_status` | Runtime, readiness, materialization, and operational status. |
| `lynchpin_catalog` | Tool actions, source contracts, schemas, and examples. |
| `lynchpin_query` | Read-only structured queries and SELECT-only SQL. |
| `lynchpin_evidence` | Graphs, claims, walks, coverage, confidence, and cross-references. |
| `lynchpin_project` | Repositories, velocity, hotspots, GitHub, reviews, and snapshots. |
| `lynchpin_personal` | Activity, signals, health, communication, web, media, and reports. |
| `lynchpin_machine` | Telemetry, pressure, services, workloads, benchmarks, and diagnostics. |
| `lynchpin_ops` | Auditable convergence operations, dry-run by default. |

Each tool contains named actions with typed metadata. Read paths remain
read-only; operations that materialize or prune state require an explicit
`execute` decision and write receipts.

## Quick start

The supported development environment is Nix-first:

```bash
direnv allow
# or
nix develop
```

Run the default test suite:

```bash
pytest -q
```

Materialize all locally rebuildable products and promote an evidence window:

```bash
python -m lynchpin.cli.materialize --all --promote \
  --start 2026-07-01 --end 2026-07-07
```

Render a current-state context pack from the promoted substrate:

```bash
python -m lynchpin.cli.current_state \
  --start 2026-07-01 --end 2026-07-07
```

Start the stdio MCP server:

```bash
python -m lynchpin.mcp
```

Commands discover their input roots through `LynchpinConfig` and environment
variables. A checkout without the private profile can still run unit tests,
inspect schemas and tool catalogs, and use neutral fixtures; source readiness
reports which live datasets are available.

## Repository guide

| Path | Responsibility |
| --- | --- |
| `lynchpin/core/` | Configuration, provenance, coverage, parsing, freshness, serialization, and shared analytical primitives. |
| `lynchpin/sources/` | Typed read APIs over owner-native data. |
| `lynchpin/ingest/` | Explicit import and canonical-product materializers. |
| `lynchpin/substrate/` | DuckDB schema, promoters, snapshots, readers, and views. |
| `lynchpin/graph/` | Evidence construction, correlations, readiness, timelines, and context packs. |
| `lynchpin/analysis/` | Code, project, personal, machine, and knowledge-analysis products. |
| `lynchpin/mcp/` | Consolidated public MCP registry, server, and internal tool implementations. |
| `lynchpin/cli/` | Stable command-line entry points. |
| `tests/` | Synthetic unit, contract, integration, and regression coverage. |
| `.lynchpin/` | Ignored local cache, substrate, generated artifacts, and operation receipts. |

## Data and trust boundaries

- Source modules do not mutate raw exports or capture databases.
- Operator-specific configuration lives under external configured roots.
- `.lynchpin/` contains regenerable local state and is not a publication
  surface.
- Polylogue owns AI-session ingestion and archive-native inference; Lynchpin
  owns cross-source promotion and correlation.
- Sinnix owns host capture and service deployment; Lynchpin owns analytical
  read models over those signals.
- Context packs and LLM-assisted interpretations cite evidence and expose
  missing or weak coverage.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — layer boundaries,
  materialization lifecycle, and product contracts.
- [`docs/reference/data-sources.md`](docs/reference/data-sources.md) — source
  roles and active adapter families.
- [`docs/reference/observability-model.md`](docs/reference/observability-model.md)
  — machine/performance evidence model.
- [`docs/reference/chisel.md`](docs/reference/chisel.md) — repository snapshots,
  growth/change-shape reports, LOC policy, and the static Beads browser.
- [`docs/lynchpin-polylogue-boundary.md`](docs/lynchpin-polylogue-boundary.md)
  — ownership boundary with Polylogue.
- [`lynchpin/analysis/METHODOLOGY.md`](lynchpin/analysis/METHODOLOGY.md) —
  evidence and reporting rules for canonical analysis.
