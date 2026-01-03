# Velocity Dashboard

## Status
experimental

## Purpose
Meta-analysis of the development process itself across the realm's project ecosystem. Provides velocity tracking (LoC growth and churn) via an interactive HTML dashboard with per-project categorization.

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
- src, tests, docs, config, generated

**sinnix** (NixOS config):
- module, host, flake, docs, other

**sinity-lynchpin** (Python analysis):
- pipelines, docs, config, other

**knowledgebase**:
- docs, config

**Simple Rust projects** (polylogue, intercept-bounce, etc.):
- src, tests, docs, config

This reveals patterns that aggregate LoC would hide (e.g., "src shrinking while tests grow").

### Interactive features
- Project selector dropdown (defaults to `all-projects` aggregate view)
- Stacked area chart for cumulative growth
- Stacked bar chart for daily churn
- Commit inspector with per-category breakdown (filters to selected series)
- Filter bar pills for quick include/exclude of categories/projects
- Zoom/pan support

### Aggregated view
The default `all-projects` view stacks each repository as its own category so
you get a single cross-repo timeline. This is intentionally uniform: each repo
is treated as a single category regardless of its internal classifier.
Use the legend or inspector toggles to include/exclude repositories and the
stats bar will recompute for the selection.

### UI scale
You can set a base UI scale via `?scale=` in the URL (e.g., `velocity.html?scale=1.4`).
The dashboard also defaults to a larger scale so browser zoom is less necessary.

## Run
```bash
just velocity
```

Or directly:
```bash
python -m lynchpin.views.velocity
```

## Adding a new project
1. Add a classifier function (or reuse existing like `classify_rust_simple`)
2. Add entry to `PROJECT_SPECS` within `lynchpin/views/velocity.py` with path, classify function, categories, and colors
