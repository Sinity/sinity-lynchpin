# Lynchpin documentation

Lynchpin keeps public documentation close to the layer that owns each
contract. This page is the short map for readers; source docstrings, schemas,
and generated catalogs provide the detailed API reference.

## System model

- [Architecture](architecture.md) explains owner-native inputs, typed source
  APIs, canonical materialization, DuckDB refreshes, evidence graphs, and
  product interfaces.
- [Data sources](reference/data-sources.md) maps configured roots and active
  adapter families to their roles and coverage boundaries.
- [Analysis methodology](../lynchpin/analysis/METHODOLOGY.md) defines the
  evidence, timeframe, denominator, calibration, and reporting rules for
  canonical analysis products.

## Project and machine evidence

- [Chisel](reference/chisel.md) documents repository snapshots, maintained-code
  attribution, Git growth and change-shape reports, Beads history, and the
  generated project packages.
- [Observability model](reference/observability-model.md) explains the machine
  telemetry substrate and the limits of resource-attribution claims.
- [Lynchpin–Polylogue boundary](lynchpin-polylogue-boundary.md) records which
  project owns AI-session ingestion, archive-native inference, cross-source
  promotion, and correlation.

## Operate and extend

- Start with the [README](../README.md) for the supported Nix-first environment,
  materialization commands, current-state packs, and MCP server.
- Use `python -m lynchpin.cli.materialize --help` to inspect dependency-ordered
  materialization and promotion options.
- Use `python -m lynchpin.mcp` for the eight-tool stdio MCP surface; tool
  schemas and examples are available through `lynchpin_catalog`.
- Run `pytest -q`, `ruff check .`, and `nix flake check` for the public quality
  gates.

Active work belongs to the committed Beads graph. Browse the
[web board](https://sinity.github.io/sinity-lynchpin/beads/) or use `bd ready`
locally.
