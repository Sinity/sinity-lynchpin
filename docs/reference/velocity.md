# Velocity Dashboard

## Status
experimental

## Purpose
Meta-analysis of development activity across the realm's code repos. Provides a static HTML dashboard for LoC growth, churn, hotspots, authorship, and co-change using project-aware categorization.

## Inputs
- Local git repos for configured projects (defined in `lynchpin/views/velocity.py`)
- Tools: `git`

## Outputs
Written under `artefacts/meta/velocity/` (ignored):
- `velocity.html` — interactive ECharts dashboard

## Features

### Per-project categorization
Each project has bespoke file classification to show meaningful breakdowns:

**sinex** (Rust):
- src, tests, docs, config

**sinnix** (NixOS config):
- module, host, flake, docs, other

**sinity-lynchpin** (Python analysis):
- analysis, tests, docs, config, other

**knowledgebase** (Markdown vault):
- docs, config, other

**Simple Rust projects** (polylogue, intercept-bounce, etc.):
- src, tests, docs, config

This reveals patterns that aggregate LoC would hide (e.g., "src shrinking while tests grow").

### Interactive features
- Project strip with aggregate view and per-repo switching
- Range controls for `30d`, `90d`, `180d`, or full history
- Pulse view with cumulative growth, daily churn/net, category share, and a date-click commit inspector
- Hotspot explorer with module/file toggle plus path search
- People view with real author aggregation from commit events and a module-ownership ledger
- Topology view with co-change graph and recent release tags
- Static single-file output: no backend required after generation

### Aggregated view
The default `all-projects` view stacks each repository as its own category so
you get a single cross-repo timeline. This is intentionally uniform: each repo
is treated as a single category regardless of its internal classifier.

## Run
```bash
python -m lynchpin.views.velocity
```

Or directly:
```bash
python -m lynchpin.views.velocity
```

Limit the render set (and aggregate view) with `--project` / `--exclude`:
```bash
python -m lynchpin.views.velocity --exclude sinnix
```

## Adding a new project
1. Add a classifier function (or reuse existing like `classify_rust_simple`)
2. Add entry to `PROJECT_SPECS` within `lynchpin/views/velocity.py` with path, classify function, categories, and colors
