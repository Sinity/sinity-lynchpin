---
status: active
purpose: explicit self-prompt pass for machine causal attribution infra
---

# Machine Causal Attribution Self-Prompt

## Context

The operator wants the spec to exploit an LLM coding agent's unusually broad
technical prior: statistics, causal inference, performance engineering, Nix,
kernel observability, experiment design, and tooling. The goal is not a longer
list. The goal is to discover missing infrastructure that would make future
empirical campaigns sharply answerable.

## Self-Prompt

I am not a normal junior developer writing a benchmark TODO list. I have broad
latent knowledge of scientific measurement, causal inference, operating
systems, performance tooling, reproducible build systems, statistical design,
and agent workflows. Force that knowledge into this problem.

Ask:

1. What would a causal-inference reviewer reject immediately?
2. What would a design-of-experiments expert add before any run happens?
3. What would a performance engineer instrument to avoid guessing?
4. What would a Nix expert require to make build-phase claims meaningful?
5. What would a statistician require before believing an interval?
6. What would a measurement-science reviewer ask about gauge error,
   censoring, and repeatability?
7. What would a skeptical future agent need to replay or refute the claim?
8. What tempting advanced method is mostly theater here and should be gated?
9. What can be learned from the existing dataset before running anything new?

## Mined Requirements

### Causal Inference

Use explicit causal graphs, not vague "confounders". The infra should store a
small DAG per candidate/plan: treatment, outcome, blocking variables,
adjustment variables, forbidden post-treatment variables, and unobserved
confounders. The support assessor can then say why a design is controlled,
natural-experiment, observational, or insufficient.

Reject: using do-calculus as decoration. The useful part is adjustment-set and
post-treatment-variable discipline.

### Design Of Experiments

Blocked randomization is necessary but not enough. If multiple factors are
possible, planner should offer factorial or fractional-factorial designs with
declared aliases, plus Latin-square/crossover order when thermal/cache carryover
matters. Sequential designs should be allowed only with predeclared interim
looks and stopping rules.

Reject: unconstrained "adaptive" reruns that keep testing until a p-value
appears.

### Performance Engineering

Wall time alone is weak. Claims should support optional instrument bundles:
Nix internal-json, machine telemetry, Linux PSI, perf stat counters, scheduler
latency where available, cgroup/service state, and build/test logs. The claim
need not require all of them, but the readiness assessor should know which
mechanistic explanations remain unobserved.

Reject: always-on heavy tracing. Use it selectively; plans declare the
instrumentation bundle.

### Measurement Science

The system needs measurement-system analysis fixtures: repeatability,
censoring/timeout handling, timer resolution, warmup/carryover, and variance
decomposition. Otherwise a wide interval can be misread as "no effect" and a
small speedup can be below timer noise.

### Statistical Inference

Intervals must respect design: paired, blocked, stratified, censored, and
hierarchical data are different. If timeouts exist, survival/censored-time
summaries are more honest than dropping failed runs. If many tests/packages are
scanned, candidate discovery and confirmatory claims need separate multiplicity
rules.

### Nix/Reproducibility

Build claims need derivation closure snapshots, substituter/cache state,
NIX_CONFIG/env digest, store/path identity, and internal-json phase provenance.
The planner should distinguish "same command" from "same derivation closure".

### Future-Agent Replay

Every claim should be replayable from rows and artifacts without reading prose.
A future agent should be able to ask: "Which assumption, if false, breaks this
claim?" and get structured answers, not narrative caveats.

### Extant Dataset Mining

The bigger near-term data source is not active experimentation; it is the
already-promoted observational corpus: work observations, machine telemetry,
episodes, service states, NixOS generations, git revisions, borg drills, GPU
regimes, and recovered history. The infra should mine it as a discovery and
quasi-experimental substrate before asking the operator to run anything.

Useful techniques:

- cohort construction by command/stage/test/package/derivation/project;
- boundary discovery over git revision, NixOS generation, hardware regime,
  service changes, cache regime, and machine episodes;
- matched controls and synthetic controls for comparable unaffected workloads;
- interrupted time series and difference-in-differences where assumptions are
  plausible;
- change-point detection as candidate discovery;
- lagged exposure models where pressure before a run matters;
- survival/censored-time summaries for timeouts and cancelled work;
- heterogeneity search to find which packages/stages are sensitive;
- anomaly clustering to identify recurring machine-context signatures.

Reject: treating the observational corpus as if it were randomized. The highest
honest output from most mined patterns is candidate or observational claim; a
natural-experiment claim needs an explicit boundary, plausible controls, and
sensitivity checks.

## Distillation

Add to the spec:

- a recursive knowledge-elicitation protocol;
- causal DAG/adjustment-set artifacts;
- DOE planner capabilities beyond simple blocking;
- instrumentation-bundle contracts;
- measurement-system analysis and censored-run handling;
- extant-dataset mining as a first-class infrastructure surface;
- mining scan registries to prevent cherry-picked winners;
- replayable assumption and threat-model fields;
- explicit gates against method theater.

## Second Pass: What Is Still Missing?

The first pass still leaned toward "claims from estimates". A serious system
also needs the substrate that makes estimates valid: leakage-proof feature
frames, explicit exposure/outcome windows, and performance-mechanism templates.

Additional mined requirements:

- build analysis feature frames with declared unit, outcome window, exposure
  windows, covariates, missingness, and provenance;
- make temporal leakage and survivorship/censoring failures validator errors,
  not caveats buried in prose;
- split extant-data mining into discovery and validation windows when coverage
  permits; use rolling-origin backtests for recurring patterns;
- record the full mining search space, not just winners, so false-discovery
  control and later review know the denominator;
- classify candidates by mechanism family: CPU, IO, memory/swap, thermal/power,
  GPU/PCIe, network/substituter/cache, service contention, scheduler latency,
  Nix eval/build phase, and test flakiness;
- require every mechanism hypothesis to carry expected signatures,
  discriminating measurements, falsifiers, and the cheapest next instrument;
- use causal discovery and ML explainers only as candidate generators, never as
  support-level upgrades.

## Tooling Pass

The repo already depends on DuckDB, Polars, NumPy, SciPy, and hmmlearn. That
should shape implementation:

- DuckDB for substrate joins, feature-frame construction, window functions,
  grouping, and coverage-aware scans;
- Polars for dataframe transforms when in-memory vectorization is clearer than
  SQL;
- NumPy/SciPy for bootstrap, rank tests, permutation tests, confidence
  intervals, robust summaries, and optimization;
- hmmlearn only for regime/candidate segmentation, not for causal claims.

Optional heavier tools should earn their place. A future statsmodels/sklearn/
ruptures/lifelines dependency is acceptable only when it replaces a fragile
local implementation and has a named consumer, fixture, and support gate.

## Implementation Phase Prompts

Use these as the durable self-prompt trail for the implemented infra phases.
Each phase prompt had to produce code, tests, graph/MCP surface, or a rejection;
otherwise it was not accepted as infrastructure.

### Dataset Mining

Prompt: "What can the existing corpus prove before any new benchmark is run,
and what must it refuse?"

Accepted:

- construct leakage-checked feature frames before mining;
- record mining denominators, discovery/validation splits, and multiplicity
  policy;
- emit cohorts, boundaries, matched comparisons, lagged exposures, anomaly
  clusters, negative controls, and instrumentation gaps as first-class
  artifacts;
- keep observational patterns at candidate or natural-experiment support unless
  explicit boundary, controls, and assumptions pass.

Rejected:

- promoting broad scans directly to causal claims;
- selecting only the most impressive mined comparisons without the scan
  denominator;
- treating missing telemetry as zero pressure, zero failures, or zero effect.

### Controlled Benchmark Infra

Prompt: "What must exist so a future run can support a controlled claim rather
than just produce logs?"

Accepted:

- generate manifest-backed plans with pre-analysis records, causal models,
  derivation identity, blocked/randomized orders, cache conditions,
  internal-json capture, and support ceilings;
- validate manifests before execution handoff;
- materialize preflight and ranked execution queues without executing them;
- require mandatory plan/run linkage before controlled support is available.

Rejected:

- ad hoc reruns until a favorable result appears;
- benchmark manifests that name commands but not derivation/cache identity;
- destructive cold-cache manipulation as a default plan step;
- claiming controlled support from dry-run templates.

### Causal Support Ladder

Prompt: "What would a skeptical reviewer need to downgrade or refuse a claim
without reading prose?"

Accepted:

- support assessments with explicit support level, refusal reasons,
  assumptions, instrumentation gaps, and next action;
- claim/refusal artifacts that preserve candidate lineage and source artifacts;
- mechanism hypotheses with expected signatures, falsifiers, discriminating
  measurements, and cheapest next instrumentation.

Rejected:

- p-values or model coefficients from convenience samples as causal support;
- SHAP, feature importance, Granger precedence, or causal discovery as support
  upgrades without an identification design;
- confidence language when the support ceiling is observational or
  insufficient.

### Graph, MCP, Status, And Context Packs

Prompt: "Can a future agent traverse from question to raw-enough evidence
without knowing the implementation history?"

Accepted:

- graph nodes and edges for feature frames, mining scans, cohorts, boundaries,
  candidates, plans, manifest groups, runs, phases, estimates, queue items,
  support assessments, claims, refusals, mechanisms, assumptions, controls, and
  gaps;
- MCP readers for every artifact family and details paths from candidate to
  manifest/preflight/support;
- status and context-pack summaries that show missing artifacts, support
  blockers, explicit refusals, and controlled-claim absence.

Rejected:

- narrative-only summaries with no artifact path or machine-readable support
  level;
- a single velocity/performance scalar that collapses workload, cache, pressure,
  package, and phase dimensions;
- a hidden second raw store. The machine layer may materialize derived artifacts
  only; raw capture remains external and replayable.

## Reversal Conditions

The rejections above are not permanent dogma. Reconsider them only if the repo
has a named consumer, fixtures, support gate, and enough data to make the method
identified rather than decorative. In particular:

- causal discovery can propose graph structure, but support still requires a
  boundary, randomized plan, or validated controls;
- heavier statistical libraries can enter when they replace fragile local code
  and are tested on null, known-effect, placebo, censoring, and missingness
  fixtures;
- heavier tracing can enter when a benchmark plan asks a mechanism question
  that wall time, internal-json phases, and existing telemetry cannot answer.
