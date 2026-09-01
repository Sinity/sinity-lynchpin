# Lynchpin

Lynchpin joins personal data that normally lives in separate systems. It reads
activity captures, terminal history, Git and GitHub data, AI-session archives,
browser and communication exports, health records, and machine telemetry. It
turns them into reproducible DuckDB datasets, evidence graphs, analysis
products, and bounded context packs for humans and agents.

Raw data stays in the system that owns it. Lynchpin builds read models with
explicit coverage, freshness, and provenance, so a result can be traced back to
the captures and exports that support it.

The project is built for one operator with a long-running local data lake. It is
not a hosted service, and private data does not belong in the repository. The
public tree contains reusable parsers, schemas, analyses, query tools, tests,
and neutral fixtures. Private identities, vocabularies, classifications,
exports, and generated narratives are supplied through external configuration.

[Project overview](https://sinity.github.io/sinity-lynchpin/) | [Documentation](docs/README.md) | [Roadmap](https://sinity.github.io/sinity-lynchpin/beads/)

## What Lynchpin can answer

Lynchpin is useful when one question depends on several sources. Examples
include:

- Which AI sessions and terminal commands preceded a commit?
- What was happening on the machine when a build or service failed?
- How did time, focus, and change volume differ across projects?
- Which evidence supports a claim about work, health, sleep, or system load?
- Which sources are missing, stale, or too weak to support a conclusion?
- What bounded context should an agent receive for a particular project or time
  window?

Source-specific tools remain authoritative. Lynchpin makes their records
joinable without erasing source boundaries.

## How it works

```text
captures, exports, repositories, service ledgers
                         |
                         v
                typed source adapters
                         |
                         v
          canonical datasets and manifests
                         |
                         v
                       DuckDB
                 |                 |
                 v                 v
          evidence graph      analysis products
                 |                 |
                 +--------+--------+
                          v
                  context packs, CLI, MCP
```

A materialization planner determines which datasets are missing or stale,
rebuilds them in dependency order, and records a manifest for each result. A
coherent time window can then be promoted into DuckDB for SQL, graph
construction, analysis, and context generation.

Refresh IDs connect graphs and reports to one specific DuckDB snapshot.
Narrative output remains downstream of the evidence rather than replacing it.

## Current sources

`lynchpin.sources` provides typed, lazy access to sources including:

- ActivityWatch application and focus events;
- Atuin commands and asciinema recordings;
- Git repositories, GitHub activity, code snapshots, and PR review details;
- Polylogue session profiles and work events;
- browser history, bookmarks, clipboard, and communication exports;
- wearable health, sleep, media, and other provider exports;
- machine metrics, service state, benchmarks, and system generations.

Adapters preserve source-specific caveats. Missing observations do not become
zero, and export-bounded datasets remain distinguishable from continuous
capture.

### Polylogue, Sinex, and Sinnix

Lynchpin reads Polylogue's stable AI-session products and a broad set of
owner-native captures and exports.

The public repository does not currently include a general adapter for Sinex's
PostgreSQL event store. Sinnix can deploy both projects on the same hosts, but
shared deployment does not merge their storage or provenance rules. A future
Sinex integration should arrive as an explicit typed source adapter with its own
coverage and freshness contract.

## Datasets and queries

The DuckDB layer provides ordinary SQL and stable readers for:

- commits, files, symbols, and review history;
- AI work events and session profiles;
- activity, focus, health, and communication signals;
- machine state, pressure, services, and experiments;
- evidence claims, graph edges, and source readiness.

It is a join and acceleration layer, not a second archive of raw data.

## Evidence graphs and context packs

The graph layer connects projects, commits, files, AI work, terminal activity,
focus spans, GitHub items, machine context, personal signals, and analysis
claims.

Current uses include:

- project and day correlation;
- session to commit correlation;
- issue, pull request, and commit closure chains;
- file and symbol overlap;
- source-readiness and confidence matrices;
- chronological current-state timelines;
- Markdown and JSON context packs with explicit weak-evidence options.

## Analysis

The analysis package covers both repositories and operator data:

- code velocity, change surfaces, dependency maps, refactor candidates, and
  cross-project comparison;
- work rhythms, focus fragmentation, AI-session efficiency, and workflow
  mechanics;
- health, sleep, mood, and other longitudinal signals;
- machine pressure, workload overlap, service behavior, experiments, and
  calibrated attribution;
- inventories of analysis artifacts and promotion of claims into evidence-backed
  products.

Canonical metrics carry a timeframe, unit or denominator, and artifact path.
Interpretive reports are expected to retain those boundaries.

## MCP

The MCP server exposes eight public tools. Each tool groups related actions
instead of exposing every internal Python function.

| Tool | Purpose |
|---|---|
| `lynchpin_status` | runtime, readiness, materialization, and operational status |
| `lynchpin_catalog` | actions, source contracts, schemas, and examples |
| `lynchpin_query` | structured read queries and SELECT-only SQL |
| `lynchpin_evidence` | graphs, claims, coverage, confidence, and cross-references |
| `lynchpin_project` | repositories, velocity, hotspots, GitHub, and snapshots |
| `lynchpin_personal` | activity, health, communication, web, media, and reports |
| `lynchpin_machine` | telemetry, pressure, services, workloads, benchmarks, and diagnostics |
| `lynchpin_ops` | auditable materialization and cleanup operations, dry-run by default |

Read paths remain read-only. Operations that materialize or remove local state
require an explicit `execute` decision and produce receipts.

## Quick start

The supported development environment is Nix-first:

```bash
direnv allow
# or
nix develop
```

Run the tests:

```bash
pytest -q
```

Materialize all locally available products and promote a time window:

```bash
python -m lynchpin.cli.materialize --all --promote \
  --start 2026-07-01 --end 2026-07-07
```

Render a current-state context pack:

```bash
python -m lynchpin.cli.current_state \
  --start 2026-07-01 --end 2026-07-07
```

Start the stdio MCP server:

```bash
python -m lynchpin.mcp
```

Commands discover data roots through `LynchpinConfig` and environment
variables. A checkout without the private profile can still run tests, inspect
schemas and tool catalogs, and use neutral fixtures. Source-readiness output
shows which live datasets are available.

## Repository guide

| Path | Purpose |
|---|---|
| `lynchpin/core/` | configuration, provenance, coverage, parsing, freshness, and shared analysis primitives |
| `lynchpin/sources/` | typed read APIs over owner-native data |
| `lynchpin/ingest/` | imports and canonical dataset materializers |
| `lynchpin/substrate/` | DuckDB schema, promotion, snapshots, readers, and views |
| `lynchpin/graph/` | evidence graphs, correlations, readiness, timelines, and context packs |
| `lynchpin/analysis/` | code, project, personal, machine, and knowledge analyses |
| `lynchpin/mcp/` | public MCP registry, server, and tool implementations |
| `lynchpin/cli/` | command-line entry points |
| `tests/` | synthetic unit, contract, integration, and regression tests |
| `.lynchpin/` | ignored local cache, DuckDB state, artifacts, and operation receipts |

## Data boundaries

- Source adapters do not mutate raw exports or capture databases.
- Operator-specific configuration lives outside the repository.
- `.lynchpin/` contains rebuildable local state and is not published.
- Polylogue owns AI-session ingestion and archive-specific interpretation.
  Lynchpin owns cross-source correlation and analysis.
- Sinex owns event capture, admission, persistence, and replay. Lynchpin does not
  currently treat the Sinex database as a general source of truth.
- Sinnix owns host configuration and service deployment. Lynchpin owns the read
  models it explicitly builds from adapted sources.
- Context packs and model-assisted reports cite evidence and expose missing or
  weak coverage.

## Documentation

- [Documentation map](docs/README.md)
- [Architecture](docs/architecture.md)
- [Data sources](docs/reference/data-sources.md)
- [Machine and performance evidence](docs/reference/observability-model.md)
- [Repository snapshots and LOC policy](docs/reference/chisel.md)
- [Polylogue boundary](docs/lynchpin-polylogue-boundary.md)
- [Analysis methodology](lynchpin/analysis/METHODOLOGY.md)

## Status

Lynchpin is a working personal analysis platform with public tests and neutral
fixtures. Its live usefulness depends on the sources configured on the
operator's machine.

## License

MIT
