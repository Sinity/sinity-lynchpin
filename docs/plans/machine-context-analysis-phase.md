# Machine Context Analysis Phase

## Purpose

Build Lynchpin's machine analysis from metric summaries into a general
troubleshooting and performance-context layer.

The target is not a one-off answer to the BIOS/dev-env performance question.
The target is a reusable analysis surface that can explain machine state during
ordinary development, focused experiments, incidents, and agent-heavy work.

## Current Substrate

Already present:

- `lynchpin.sources.machine` reads canonical live telemetry SQLite samples.
- Substrate tables preserve metric, GPU, network, service-state, and experiment
  rows under refresh IDs.
- `lynchpin.analysis.machine.telemetry` produces coverage, daily rollups,
  hardware regimes, trends, anomalies, and metric correlations.
- `lynchpin.analysis.machine.below` summarizes bounded below exports without
  treating `/var/log/below/store` as a data lake.
- MCP exposes daily machine metric and service-state summaries.

Current state:

The analysis layer can detect episodes, join them to work/activity windows,
attach bounded below captures, build observational baselines, and produce
manifest-backed experiment claim packs. The remaining work is consolidation:
make sure these stable outputs appear in the current-state/context-pack surface
and keep refining statistical quality as the dataset grows.

## Phase Shape

### 1. Machine Episode Model

Add a typed episode detector over `machine_metric_sample`,
`machine_gpu_sample`, `machine_network_sample`, and `machine_service_state`.

Episode kinds:

- `cpu_saturation`
- `memory_pressure`
- `io_pressure`
- `scheduler_latency`
- `blocked_task_pressure`
- `gpu_power_or_thermal`
- `gpu_link_regime`
- `network_degraded`
- `service_instability`

Each episode should carry:

- start/end timestamps
- severity and confidence
- triggering metrics and thresholds
- source row counts
- affected host
- caveats for missing or sparse dimensions

Output:

- Python API: `lynchpin.analysis.machine.episodes`
- JSON artifact: `machine_episode_analysis.json`
- MCP read tool: `machine_episodes`

Acceptance:

- Synthetic tests cover thresholding, gap merging, sparse-data caveats, and
  multi-source severity scoring.
- No hardcoded "performance question" labels appear in the episode model.

### 2. Workload Window Join

Create a work-context join that overlays machine episodes onto development
activity windows.

Input windows:

- Polylogue session profiles and work events
- terminal sessions
- ActivityWatch focus/deep-work spans
- git commit sessions
- experiment manifests

Output rows should answer:

- what was running
- which project(s) were active
- which agent/provider/work kind was involved
- what machine episodes overlapped the window
- whether the overlap is observational only or part of a controlled run

Output:

- Python API: `lynchpin.analysis.machine.context`
- JSON artifact: `machine_context_windows.json`
- CLI command: `python -m lynchpin.analysis machine-context`
- MCP read tool: `machine_context_windows`

Acceptance:

- Window joins preserve source dimensions instead of collapsing into one score.
- Tests cover partial overlaps, timezone handling, and windows with no machine
  coverage.

### 3. Below Attribution Windows

Promote bounded below exports from standalone summaries into attribution
evidence attached to episodes and context windows.

Rules:

- Do not promote the live below store wholesale.
- Treat below exports as bounded incident/experiment windows.
- Join by timestamp overlap and capture ID.
- Keep process and cgroup attribution separate from machine metric causes.

Output:

- Python API: `lynchpin.analysis.machine.attribution`
- JSON artifact: `machine_below_attribution.json`
- CLI command: `python -m lynchpin.analysis machine-below-attribution`
- process/cgroup top contributors on machine episodes
- "unattributed" caveat when machine pressure exists without below coverage
- capture window inventory and coverage report

Acceptance:

- Tests prove that below attribution can enrich an episode without changing the
  original machine metric evidence.

### 4. Experiment Claim Packs

Turn `machine_experiment_run` into a claim-pack generator for controlled
benchmark statements.

Claim packs must include:

- randomized manifest identity
- treatment/control labels
- workload identity
- git state
- pre/post machine state
- joined telemetry window
- excluded rows and caveats
- effect estimates with confidence limits where sample size permits

Output:

- Python API: `lynchpin.analysis.machine.experiments`
- JSON artifact: `machine_experiment_claims.json`
- CLI command: `python -m lynchpin.analysis machine-experiments`
- MCP read tool: `machine_experiment_claims`

Acceptance:

- Benchmark claims require a manifest-backed run.
- Observational claims are allowed, but must be labeled observational and must
  not use controlled-benchmark language.

### 5. Observational Performance Baselines

Use the continuously gathered dataset for exploratory analysis.

Baselines:

- by hour of day
- by project
- by work kind
- by agent/provider intensity
- by service/cgroup state
- by hardware regime

Analyses:

- robust medians and MAD/IQR bands
- changepoints
- anomaly runs
- lagged correlations
- before/after comparisons around known fixup dates

Output:

- Python API: `lynchpin.analysis.machine.baselines`
- JSON artifact: `machine_observational_baselines.json`
- CLI command: `python -m lynchpin.analysis machine-baselines`
- narrative-ready summaries for context packs

Acceptance:

- Baselines report coverage and confounding caveats.
- Comparisons across BIOS/dev-env eras refuse to claim causality unless backed
  by experiment manifests.

## Work Order

1. Episode detector.
2. Workload window join.
3. Below attribution joins.
4. Experiment claim packs.
5. Observational baselines.
6. MCP and current-state integration for the stable outputs.

This order builds the foundation first: machine episodes become the shared
primitive used by context joins, below attribution, experiments, and baselines.

## Non-Goals

- No compatibility wrappers or retired aliases.
- No wholesale below ingestion.
- No new warehouse that duplicates raw captures.
- No single "performance score".
- No causal language for observational-only evidence.

## Verification Gate

Before calling the phase complete:

- `ruff check lynchpin tests`
- focused tests for machine analysis, substrate promotion, MCP tools, and
  current-state integration
- `just typecheck`
- one live artifact generation run against the current substrate
- a repo-coherence scan for retired machine-analysis paths or compatibility
  aliases introduced during the phase
