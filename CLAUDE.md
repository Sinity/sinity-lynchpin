# Lynchpin Agent Contract

> Repository instructions for Lynchpin. `AGENTS.md` is a committed symlink to
> this file. Keep this document flat, current, and focused on editing rules;
> architecture and source catalogues belong in `docs/`.

## Mission

Lynchpin is a local-first evidence and analysis platform. It reads heterogeneous
captures, exports, repositories, and project-native ledgers through typed source
adapters; builds reproducible materialized products and a coherent DuckDB read
model; and exposes evidence graphs, analyses, context packs, CLI commands, and
MCP tools.

Raw inputs remain owned by their capture system or provider export. The
repository contains reusable code, schemas, contracts, and neutral fixtures—not
the operator's datasets or generated personal results.

## Working Stance

- Preserve evidence semantics. Missing coverage is not zero activity, an
  association is not causation, and a generated narrative is not a source.
- Prefer one canonical route. Remove superseded adapters, aliases, plans, and
  parallel representations when their replacement is established.
- Read related source, promoter, substrate, graph, MCP, and test paths before
  changing a cross-layer contract.
- Batch related edits, run focused production-route tests, then run the broad
  gate once at the commit boundary.
- Commit and push verified work directly to `master` unless the operator or an
  active workflow says to hold.

## Beads

Use `bd` for durable project work:

```bash
bd prime
bd ready --json
bd show <id> --json
bd update <id> --claim --json
bd close <id> --reason "..." --json
```

Create a linked Beads item for discovered work that will outlive the current
change. `issues.jsonl` is a reviewed public technical record; interaction logs
and local database state are not publication surfaces.

## Public Repository Boundary

Treat every tracked file, commit message, branch, tag, Beads issue, CI log, and
GitHub discussion as public.

- Generic adapters, schemas, analyses, and neutral synthetic fixtures belong in
  Git. Secrets, private datasets, captures, exports, transcripts, narratives,
  identity profiles, and unrelated personal information do not.
- Operator-specific identities, vocabularies, classifications, life events, and
  filesystem roots come from external configuration.
- `.agent/`, root `.claude/`, `.lynchpin/`, and
  `.beads/interactions.jsonl` are local-only.
- Before committing, review the complete staged diff as public content. A path
  or regex check cannot decide whether arbitrary prose or fixture data is safe.
- If there is any doubt whether material belongs in the public repository,
  confirm with the operator before committing it.
- Publish only `master`. Never push `--mirror`, `--all`, or `--tags`; review any
  proposed additional ref independently.
- If private material enters history, stop publication and rewrite the allowed
  branch. A later deletion does not remove the historical blob or message.

## Architecture

```text
owner-native inputs
        │
        ▼
lynchpin/sources/       typed, lazy, coverage-aware adapters
        │
        ▼
lynchpin/ingest/        explicit canonical-product materializers
        │
        ▼
lynchpin/substrate/     coherent DuckDB refreshes and stable readers
        │
        ├──────────────► lynchpin/graph/      evidence and context packs
        └──────────────► lynchpin/analysis/   deterministic analyses
                                      │
                                      ▼
                           CLI and eight-tool MCP contract
```

Key ownership boundaries:

- `core/` owns configuration, provenance, coverage, serialization, date/time
  primitives, and shared analytical types.
- `sources/` owns source-local discovery, parsing, normalization, and caveats.
- `ingest/` owns explicit imports and rebuildable canonical products.
- `substrate/` owns the derived DuckDB schema, promotion, refresh coherence,
  snapshots, views, and readers.
- `graph/` owns cross-source relations, readiness, timelines, movement, and
  context packs.
- `analysis/` owns deterministic project, personal, machine, and ecosystem
  products.
- `mcp/` owns the consolidated public tool registry and internal action routes.
- Polylogue owns AI-session ingestion and archive-native inference; Lynchpin
  owns promotion and cross-source analysis over its stable products.

## Data and Analysis Invariants

1. Source modules are read APIs. Ordinary analysis does not mutate owner-native
   inputs.
2. DuckDB is a rebuildable read model, never a replacement for raw evidence.
3. A coherent query uses one `refresh_id`; do not mix generations silently.
4. Export-bounded coverage and continuous-capture gaps are different states.
5. Use `core.primitives.logical_date` for logical-day bucketing instead of raw
   `datetime.date()` calls.
6. Cross-source joins belong downstream of source-local normalization.
7. Canonical claims identify artifact/refresh, timeframe, denominator, method,
   and degraded or missing coverage.
8. MCP read actions remain read-only. Convergence or maintenance operations are
   explicit, auditable, dry-run by default, and return receipts.
9. Use typed boundary errors where callers need to distinguish unavailable
   sources, schema mismatch, coverage failure, and materialization failure.
10. LLM synthesis may explain measured products; it may not redefine them.

## Development Workflow

The supported environment is Nix-first:

```bash
direnv allow
# or
nix develop
```

Use the wrapped command names inside the shell so long-running work receives the
configured resource containment:

```bash
pytest -q                         # default non-live test suite
just lint                        # Ruff
just typecheck                   # strict maintained mypy slice
just check                       # lint + typecheck + tests
python -m lynchpin.mcp --self-check
python -m lynchpin.cli.materialize --help
python -m lynchpin.cli.current_state --start YYYY-MM-DD --end YYYY-MM-DD
```

Do not invoke tests through `.venv/bin/python` or `python -m pytest`; that
bypasses the devshell's command-name resource wrapper. Live/private integrations
remain explicitly marked and are excluded from the default suite.

When changing a contract:

- source change → check its typed models, coverage/readiness, materializer, and
  source tests;
- substrate change → check schema version, promoter, reader/view, snapshot
  coherence, MCP route, and migration/rebuild behavior;
- graph/analysis change → check provenance, weak-evidence behavior, refresh
  selection, and generated-artifact location;
- MCP change → update the internal action implementation, public registry
  metadata, contract tests, and the eight-tool self-check together.

Generated products belong under the configured derived root or ignored
`.lynchpin/` state. Do not commit generated personal analyses, local caches,
substrate databases, operation receipts, or scratch packets.

## Documentation

| Topic | Canonical document |
| --- | --- |
| External overview and quick start | `README.md` |
| Layer boundaries and lifecycle | `docs/architecture.md` |
| Source roles and families | `docs/reference/data-sources.md` |
| Machine/performance evidence | `docs/reference/observability-model.md` |
| Polylogue ownership boundary | `docs/lynchpin-polylogue-boundary.md` |
| Canonical analysis rules | `lynchpin/analysis/METHODOLOGY.md` |

Update the owning document when a contract changes. Do not add tracked planning
documents, generated reports, migration diaries, or duplicate API catalogues.
