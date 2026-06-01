# Machine Causal Attribution Infrastructure Spec

Status: implementation spec
Scope: all useful Lynchpin infrastructure for machine/work attribution,
benchmark planning, future benchmark ingestion, and support-level claims
Excludes: Polylogue repair, executing real benchmark campaigns, final empirical
judgment over future benchmark results

## Done Boundary

This work is complete when future machine attribution work can proceed by
choosing or collecting data, not by adding new infrastructure.

Concretely, given the existing machine/work dataset or a future slowdown
candidate, the system must be able to:

1. mine extant work/machine data into cohorts, boundaries, candidate patterns,
   and natural-experiment opportunities;
2. expose each candidate through MCP/current-state/evidence graph;
3. generate a valid dry-run controlled benchmark plan and manifests when the
   best next action is an active experiment;
4. later ingest executed run manifests, Nix internal-json logs, test/build
   outputs, and telemetry windows;
5. compute observational, natural-experiment, or controlled estimates and
   confidence intervals;
6. promote a support-level claim or a structured refusal into `analysis_claim`;
7. show the evidence chain, caveats, and support level through MCP, graph, and
   context-pack/dossier surfaces.

After this boundary, substantial remaining work for this slice should be
empirical interpretation and optional campaigns, not missing mining surfaces,
schema, parsers, runners, claim plumbing, graph edges, MCP tools, or refresh
wiring.

## Top-Down Design

This is a local scientific attribution system over machine/work evidence. Its
center is not a benchmark runner; it is a claim pipeline that keeps evidence,
assumptions, support level, and refusal reasons attached from raw observation
through final explanation.

Read the system as seven layers:

| Layer | Owns | Must Not Own |
|---|---|---|
| Source/substrate | typed rows, coverage, refresh status | claims or interpretation |
| Frame | analysis units, exposure/outcome windows, leakage checks | candidate ranking |
| Mining | scans, cohorts, boundaries, controls, gaps | causal wording |
| Mechanism | signatures, falsifiers, next instruments | statistical support |
| Candidate | prioritized questions and benchmark sketches | claims |
| Estimate/claim | estimands, intervals, assumptions, support/refusal | narrative synthesis |
| Surfaces | MCP, graph, current-state, dossiers | hidden mutation or support upgrades |

Canonical dataflow:

```text
raw captures / source artifacts
  -> substrate rows + coverage/readiness
  -> feature frames + mining scan registry
  -> cohorts / boundaries / controls / mechanisms / gaps
  -> candidates with score components and support ceiling
  -> plan OR observational/natural estimate
  -> claim/refusal with assumption ledger
  -> graph + MCP + context/dossier explanation
```

The dependency direction is one-way. Narratives cannot create claims. MCP tools
cannot silently execute benchmarks. Candidates cannot become claims without an
estimate and support assessor. Estimates cannot bypass feature-frame leakage,
coverage, search-denominator, and assumption-ledger checks.

The primary near-term path is:

```text
existing dataset -> mining spine -> candidate/refusal/observational outputs
```

The controlled-benchmark path is a second path:

```text
candidate -> pre-analysis plan -> manifest-backed runs -> controlled estimate
```

The second path is important because it can produce the strongest support. It
is not the default first move when the existing dataset can already narrow the
question or prove that the right next action is better instrumentation.

## First Vertical Slice

Do not start by implementing every method in the catalog. Prove the spine with
one narrow but complete path:

1. choose one existing high-volume work surface, such as `xtask` stage duration;
2. build one feature frame with explicit unit, exposure window, outcome window,
   missingness, censoring, coverage, and leakage validation;
3. register one mining scan over that frame, including search denominator and
   multiplicity policy;
4. emit cohorts, one boundary or pressure comparison, one negative control, and
   one instrumentation gap if the data cannot answer cleanly;
5. classify a mechanism hypothesis with expected signatures and falsifiers;
6. promote one candidate or insufficient-support refusal with score components,
   support ceiling, and assumption ledger;
7. expose the chain through graph and MCP;
8. show it in current-state/dossier output without adding support in prose.

This slice is successful only if a future agent can start from the MCP
candidate, follow every edge to source rows and assumptions, and understand
whether the next action is more mining, instrumentation, or a controlled
benchmark plan.

## Implementation Shape

Keep implementation aligned with the layers. Prefer small typed modules that
produce one durable artifact or reader each.

Expected module map:

| Layer | Likely Home | Product |
|---|---|---|
| Refresh/joins | `lynchpin.analysis.machine.work_observations`, `context` | work windows, parent/child views |
| Feature frames | `lynchpin.analysis.machine.feature_frames` | `machine_analysis_feature_frames.json` and substrate rows |
| Mining scans/cohorts | `lynchpin.analysis.machine.mining` | scan registry, cohort inventory |
| Boundaries/comparisons | `lynchpin.analysis.machine.comparisons` | boundaries, matched comparisons, controls |
| Mechanisms/assumptions | `lynchpin.analysis.machine.mechanisms` | mechanism hypotheses, assumption checks |
| Candidates | `lynchpin.analysis.machine.attribution_candidates` | candidate queue |
| Estimates/claims | `lynchpin.analysis.machine.estimates`, `attribution_claims` | estimates, claims, refusals |
| Plans/runs | `lynchpin.analysis.machine.benchmarks`, `controlled_benchmarks` | plans, manifests, readiness |
| Graph/MCP | `lynchpin.graph.machine_analysis`, `lynchpin.mcp.tools.machine` | evidence nodes, read tools |

Rules:

- each analysis module returns typed dataclasses and writes deterministic JSON
  through the existing analysis-artifact path;
- any artifact that becomes an MCP or graph source must also have a refresh
  step and readiness status;
- substrate tables are for repeated joins and MCP latency, not a second raw
  store;
- graph payloads remain compact and point to artifact/source ids for detail;
- avoid cross-layer imports upward. Lower layers must not import graph, MCP,
  current-state, or dossier modules.

## Anti-Slop Rules

Do not add generic abstractions, empty manifests, or decorative metadata.

Every new table, artifact, manifest field, graph node, MCP tool, or analysis
module must answer all of these:

1. **Consumer**: what code or operator decision reads it?
2. **Decision**: what choice does it change?
3. **Validity**: what support-level rule does it enforce?
4. **Failure mode**: what refusal or caveat is emitted when it is absent,
   malformed, or weak?
5. **Replacement test**: if it were deleted, which workflow would become
   impossible or meaningfully less reliable?

Reject work that only names a concept without producing one of:

- a parsed measurement;
- a deterministic transform;
- a statistical estimate;
- a support-level decision;
- a graph relation;
- an operator action.

Prefer fewer, stronger artifacts over many parallel summaries. If two artifacts
cannot name different consumers, merge them.

## Recursive Knowledge Elicitation

Major revisions and implementation phases must include a short written
self-prompt pass before adding new infra. The pass must ask what a
causal-inference reviewer, design-of-experiments expert, performance engineer,
Nix expert, statistician, measurement-science reviewer, and skeptical future
agent would reject.

The self-prompt pass is valid only if it produces accepted and rejected
requirements. Accepted requirements must land in this spec or code with a
consumer and validity gate. Rejected ideas must name why they are method
theater, too costly, unsupported by available data, or better deferred to
empirical execution.

Current self-prompt artifact:

- `docs/plans/machine-causal-attribution-self-prompt.md`

Current non-execution acceptance audit:

- `docs/plans/machine-causal-attribution-audit.md`

## Non-Goals

- **Polylogue repair**: degraded Polylogue products must be skipped honestly.
  Do not synthesize semantic chat evidence as a replacement.
- **Actual benchmark execution**: dry-run planning and future ingestion paths
  are in scope; running campaigns is not.
- **Remote services**: all data and analysis remain local.
- **Narrative-first causality**: narratives may summarize claims, but never
  create support level.

## Support Levels

Every surface must preserve this ladder:

| Level | Meaning | Allowed Output |
|---|---|---|
| Observation | measured event or aggregate | telemetry/work rows, summaries |
| Candidate | non-causal pattern worth investigating | ranked queue, benchmark sketch |
| Natural experiment | non-randomized comparison across a real boundary | caveated claim |
| Controlled | randomized, manifest-backed comparison | support-level claim |
| Insufficient | attempted analysis failed support requirements | refusal/negative claim |

Rules:

- Observations and candidates are never causal claims.
- “Controlled” requires a valid controlled benchmark manifest plus estimable
  control/treatment samples.
- Invalid manifests may produce observational packs or refusals, not controlled
  claims.
- Any natural-experiment claim must name the boundary and non-randomization
  caveat.

## Scientific Practice Contract

The machine-attribution layer operates in Lynchpin hard-data mode. Every
top-line number must carry artifact path, timeframe/window, denominator, unit,
and coverage bounds. Missing data is missing, never zero.

Every benchmark plan or claim candidate must include a compact pre-analysis
record:

- research question;
- hypothesis and expected direction;
- estimand, separate from estimator;
- unit of analysis: run, derivation, phase, package, test, or window;
- primary metric and secondary metrics;
- minimum effect of interest;
- inclusion and exclusion rules;
- stopping rule or fixed repeat count;
- blocking/stratification keys;
- confounders expected by design;
- negative controls or placebo windows when available;
- exact support level that the design can possibly reach.

The pre-analysis record is not bureaucracy. It is the guardrail against
rewriting a benchmark after seeing the result. Exploratory analysis remains
allowed, but it must be labeled exploratory and cannot silently promote itself
to controlled support.

Every claim must name what would have falsified or refused it:

- data-quality refusal: missing coverage, timestamps, internal-json, or cache
  proof;
- design refusal: no randomization, broken blocks, changed workload mix, or
  no comparable control;
- statistical refusal: interval too wide, underpowered run, unstable
  sensitivity result, or multiple-comparison failure;
- provenance refusal: source artifact cannot be traced back to raw capture or
  immutable manifest.

Validity requirements:

- cross-source or orthogonal confirmation is required for natural-experiment
  claims whenever the source exists. A single-source finding remains
  `observational` or `candidate`;
- source coverage must be propagated to all estimates and prose. Export bounds
  are not freshness failures; capture gaps are capture faults;
- bare counts are insufficient when volume/duration/severity is the meaningful
  denominator;
- raw-log silence, missing exports, and absent rows are not semantic signals
  unless the source contract says the capture is continuous over that window;
- generated prose may compress evidence, but cannot add support that is not
  present in the claim artifact.

## Implemented Baseline

The current branch already provides:

- `xtask_history` reader over live and recovered ledgers;
- substrate tables for `work_observation`, `work_observation_stage`, and
  `work_observation_test_result`;
- promotion of work observations, stages, and test results;
- work-observation analysis artifact and read models;
- MCP tools for daily work observations, stage summaries, test summaries, the
  work-observation artifact, and attribution candidates;
- `xtask_history` source readiness;
- context-pack/status integration for `machine_work_observations.json` and
  `machine_attribution_candidates.json`;
- evidence-graph nodes of kind `machine_work_observation`;
- controlled benchmark manifest validator;
- deterministic bootstrap delta estimator;
- experiment claim packs that refuse controlled language unless the manifest
  contract is satisfied;
- attribution candidates from extant observational data.

Latest canonical refresh established:

- `3,622` work-observation rows;
- `6,905` stage rows;
- `43,340` test-result rows;
- `20` attribution candidates.

Current candidates are stage/work-observation driven because the refreshed
`machine_observational_deltas.json` has zero pressure-vs-quiet deltas.

## Extant Dataset Mining

The largest immediate data source is the existing observational corpus, not
new benchmark execution. The system should mine that corpus exhaustively for
candidate mechanisms, natural experiments, and high-value benchmark targets.

Inputs:

- `work_observation`, `work_observation_stage`, and
  `work_observation_test_result`;
- `machine_metric_sample`, `machine_gpu_sample`, `machine_network_sample`, and
  `machine_service_state`;
- machine episodes, context windows, borg drills, NixOS generations, hardware
  regimes, git revisions, and generated analysis artifacts;
- recovered historical windows where coverage is explicit.

Required mining products:

- mining scan registry: metrics, cohorts, filters, windows, methods, and
  candidate counts searched before ranking;
- discovery and validation window plan for mined patterns when coverage permits;
- cohort inventory by command, stage, test, package, derivation, project,
  revision, cache condition, pressure state, host regime, and service state;
- leakage-proof feature frames with explicit unit, exposure windows, outcome
  windows, covariates, missingness indicators, and source provenance;
- boundary inventory for git revisions, NixOS generations, hardware regimes,
  service changes, cache regime changes, and operator workflow transitions;
- change-point candidates over duration, failures, pressure, and phase timing;
- matched comparison sets with explicit match keys and unmatched-count caveats;
- negative-control and placebo comparisons for unrelated packages, stages,
  projects, or pre-boundary windows;
- heterogeneity summaries identifying which stages/tests/packages are sensitive
  to the same suspected factor;
- lagged exposure summaries for pressure before and during work windows;
- anomaly clusters for recurring machine-context signatures;
- mechanism hypotheses that map observed signatures to falsifiable performance
  mechanisms;
- instrumentation-gap candidates where the data cannot answer the question.

Mining outputs may promote:

- `candidate`: most mined patterns;
- `observational`: descriptive association with support limits;
- `natural_experiment`: only when a real boundary, comparable controls,
  coverage, and sensitivity checks exist;
- `insufficient`: when the dataset cannot support the hypothesis and the next
  action is instrumentation or a controlled plan.

Mining must be breadth-first before hand-picked interpretation: produce the
inventory of cohorts, boundaries, gaps, and top candidates before writing
narrative explanations.

### Missing Analysis Surface Audit

Before declaring the infrastructure complete, run a systematic source-by-source
audit for data that is parseable, local, and decision-relevant but not yet
represented in contracts, substrate rows, graph nodes, MCP tools, or current
state. A source is conspicuously missing when all of these are true:

- it has durable local artifacts with timestamps or windows;
- it describes work, machine state, verification, decisions, costs, or
  operator behavior;
- it can improve cohorting, boundary detection, mechanism falsification,
  retrospective controls, or claim caveats;
- Lynchpin only sees it through ad hoc file reads, scratch notes, or not at all.

Minimum audit axes:

| Axis | Question | Expected Product |
|---|---|---|
| Source inventory | Which local datasets exist but have no `SourceContract`? | contract/backlog row with authority, coverage, caveats |
| Temporal grain | Is it point event, interval, daily aggregate, or artifact snapshot? | typed source row with explicit grain |
| Work linkage | Can it attach to project/command/revision/session? | `work_observation`, graph edge, or refusal reason |
| Machine linkage | Can it overlap machine episodes or Below windows? | bounded window with host and resource fields |
| Graph linkage | Does it create evidence nodes or only status text? | graph node/edge mapping or explicit exclusion |
| Analysis linkage | Which mining method would consume it? | cohort, boundary, mechanism, control, or gap |
| Surface linkage | Can an MCP/current-state consumer discover it? | MCP/readiness/status exposure |

Current conspicuous source class: repo-local development tooling histories.
Sinex `xtask_history` is integrated; Polylogue has an equivalent primitive
ledger at `.agent/xtask/tasks.jsonl` and older run logs/metrics under
`.local/logs`. These must be integrated as `polylogue_devtools`, promoted into
`work_observation`, and cross-referenced against machine/Below attribution so
verification-heavy Polylogue work becomes part of the same retrospective
analysis corpus as Sinex.

Discovery and validation are separate roles. A pattern discovered by scanning
the full corpus remains exploratory unless it is checked against a held-out
time slice, a later rolling-origin window, a negative control, or a controlled
benchmark plan.

## Lifecycle

The infrastructure must implement the full lifecycle below. Each stage has a
typed artifact, graph surface, MCP surface, and refresh status.

| Stage | Input | Output | Support Level |
|---|---|---|---|
| Observe | substrate/source rows | work and machine summaries | observation |
| Mine | extant observations | cohorts, boundaries, controls, gaps | observation/candidate |
| Detect | mined products, summaries, deltas, boundaries | attribution candidates | candidate |
| Plan | candidate, workload spec | dry-run benchmark plan/manifests | candidate |
| Execute | future operator run | run manifests/logs/telemetry | observation |
| Ingest | executed artifacts | promoted run/phase/result rows | observation |
| Estimate | promoted run groups | effect estimates/intervals | candidate or claim input |
| Claim | estimates + readiness | `analysis_claim` row or refusal | controlled/natural/insufficient |
| Explain | graph/MCP/context packs | evidence chain and caveats | same as claim |

Artifacts are lifecycle products, not storage decoration. A product is valid
only if it advances at least one row of this lifecycle.

## Core Data Contracts

### Work Observations

`work_observation` is one timed invocation. It carries source id, command, cwd,
project, timestamps, duration, status, exit code, host, git state, live stage,
and resource-pressure summaries. It is operator telemetry, not controlled
evidence.

`work_observation_stage` is a timed child of an invocation. It is the primary
source for stage-level slowdown candidates.

`work_observation_test_result` is a child of an invocation, but currently lacks
independent timestamps. Time-window filtering must go through promotion window
or parent invocation joins.

Required new read models:

- invocation-to-stage join;
- invocation-to-test join;
- stage daily/project summary;
- slow stage summary by project/command/git revision;
- slow test/package summary;
- failure taxonomy by command, stage, package, failure type, exit code;
- package membership view that is explicitly non-timed unless parent joined.

### Machine Context

Machine context includes telemetry samples, episodes, service state, GPU/network
regimes, NixOS generations, borg drills, benchmark manifests, and work windows.

Required new read models:

- substrate-backed workload windows;
- work window to machine episode overlap;
- benchmark run to telemetry window overlap;
- run to NixOS generation/hardware regime;
- refresh health and coverage by machine source.

### Mined Dataset Products

Mined products are derived read models over existing rows. They are not
new raw stores.

Required products:

- `machine_observation_cohort`: stable cohort key, dimensions, row count,
  coverage window, outcome summaries, and caveats;
- `machine_mining_scan`: scan id, search space, metrics, filters, methods,
  comparison universe size, emitted candidate ids, multiplicity policy, and
  caveats;
- `machine_analysis_feature_frame`: frame id, unit type, unit ids, outcome
  metric/window, exposure windows, covariates, missingness flags, provenance,
  and leakage policy;
- `machine_discovery_validation_split`: discovery window, validation window,
  split rationale, row counts, coverage, and leakage caveats;
- `machine_boundary_candidate`: boundary id, boundary type, timestamp/date,
  affected dimensions, pre/post coverage, candidate controls, and caveats;
- `machine_matched_comparison`: treated cohort, control cohort, match keys,
  unmatched counts, balance diagnostics, estimate input, and support ceiling;
- `machine_negative_control`: placebo dimension/window, expected-null
  rationale, result, and interpretation;
- `machine_instrumentation_gap`: hypothesis, missing field/source/window, and
  next instrumentation or benchmark action.

These products feed candidate generation, natural-experiment estimation,
MCP readers, graph nodes, and dossier summaries. If a product has no such
consumer, it should not exist.

Feature-frame rules:

- every row declares its analysis unit: invocation, stage, test, package,
  derivation, day, or bounded machine window;
- outcome windows and exposure windows are separate fields;
- covariates used as pre-treatment controls must end before the outcome window;
- concurrent pressure features must be labeled as concurrent context, not
  pre-treatment cause;
- post-treatment variables are flagged and excluded from adjustment sets;
- missingness is represented explicitly, never imputed silently;
- censored rows from timeout/cancelled work remain in the frame with censoring
  metadata.

### Mechanism Library

Candidates should map to falsifiable mechanism families, not only labels like
“slow” or “pressure”.

Required mechanism templates:

- CPU saturation/run-queue contention;
- IO contention or uninterruptible waits;
- memory pressure, swap, or OOM-adjacent behavior;
- thermal throttling, power governor, or frequency cap;
- GPU power/thermal/PCIe regime;
- network, substituter, or remote-cache instability;
- systemd service/cgroup contention;
- scheduler latency or timer oversleep;
- Nix eval versus build versus test-phase bottleneck;
- test flakiness or package-specific failure mode.

Each template must define:

- expected signatures in existing data;
- discriminating measurements that separate it from adjacent mechanisms;
- falsifiers;
- support ceiling with current data;
- cheapest next instrument or controlled benchmark plan.

### Benchmark Runs

`machine_benchmark_plan` is the durable pre-analysis record. It is created
before execution and stores question, hypothesis, estimand, estimator, unit,
metric set, minimum effect, exclusion rules, stopping rule, block keys,
negative controls, support ceiling, random seed, workload snapshot, and
validation status.

Each plan also carries a compact causal model:

- treatment variable;
- outcome variable;
- blocking variables;
- adjustment variables;
- forbidden post-treatment variables;
- known unobserved confounders;
- identification note explaining why the requested support level is possible
  or impossible.

`machine_experiment_run` remains the manifest-backed run table. Future executed
benchmark ingestion must add or derive:

- benchmark plan/group;
- run phase timings from Nix internal-json;
- workload result rows for tests/builds;
- run telemetry overlap summaries;
- estimate rows or artifact sections.

The raw manifest/log paths remain provenance; heavy logs do not belong inline in
graph nodes.

Plan/run linkage is mandatory. A run without a prior plan is observational data
only unless a later operator explicitly records it as retrospective exploratory
analysis.

### Claims

Machine attribution claims use the generic `analysis_claim` substrate with:

- `claim_type = "machine_attribution"`;
- support level one of `controlled`, `natural_experiment`, `observational`,
  `insufficient`;
- source ids and relation ids where available;
- caveats preserving every support-limiting condition;
- payload with metric, suspected factor, baseline, comparison, estimate, and
  readiness.

Every non-controlled claim must carry an assumption ledger:

- assumption id and plain-language statement;
- claim scope affected by the assumption;
- check status: passed, failed, untestable, or not checked;
- evidence artifact or row ids used for the check;
- sensitivity result when the assumption is weakened or removed;
- support consequence if the assumption fails.

Untested assumptions do not automatically invalidate observational outputs, but
they cap natural-experiment support unless the claim explains why the
assumption is not material.

## Controlled Benchmark Contract

A manifest is controlled only if it satisfies all required fields:

| Field | Requirement |
|---|---|
| `run_group_id` | stable group tying randomized runs together |
| `derivations` | fixed workload/derivation set; each row has `drv_path`, `store_path`, or `name` |
| `cache_conditions` | includes both `cold` and `warm` |
| `assignment_seed` | randomization seed |
| `randomized_order` | concrete randomized run sequence |
| `control_label` | baseline condition |
| `treatment_label` | treatment condition |
| `internal_json.path` | Nix internal-json capture path |
| `telemetry.window_source` | initially `manifest_timestamps` |
| `pre_analysis` | scientific-practice record with estimand, metric, exclusion, stopping, and support ceiling |

Recommended fields:

- metric and expected direction;
- host, hardware regime, software revision;
- pre/post state;
- notes.

Explicitly insufficient for controlled support:

- `randomized: true` alone;
- control/treatment labels without run order;
- fixed derivations without cache evidence;
- benchmark timestamps without telemetry overlap;
- Nix build-phase claim without internal-json provenance.

## Benchmark Planning Infrastructure

Add a dry-run benchmark planner. It must not execute builds or tests.

Inputs:

- candidate id or explicit workload spec;
- command template;
- derivation/workload set;
- control/treatment labels;
- cache-condition matrix;
- repeat count;
- random seed;
- blocking keys, when relevant: derivation, package, cache condition, host,
  software revision;
- target metric and minimally interesting effect size;
- output root.

Outputs:

- group-level `plan.json`;
- embedded pre-analysis record;
- per-run `manifest.json`;
- randomized order;
- derivation snapshot;
- expected internal-json log paths;
- expected telemetry-window linkage;
- power/sample-size note for the target metric, even if approximate;
- validation report using the same controlled benchmark validator used by claim
  analysis.

The planner must make future execute mode a direct extension: execute fills
actual start/end timestamps, exit status, internal-json path, telemetry overlap,
pre/post state, and output paths.

Generated plans must support blocked randomization. A plan that mixes
derivations, packages, cache conditions, or hosts must randomize within blocks
or explicitly mark the design as insufficient for controlled support.

Plans should be pre-registrations for the benchmark: target metric, estimator,
minimum effect of interest, exclusion rules, blocking keys, and stopping rule
are written before execution. Post-hoc exploratory analysis may exist, but it
cannot silently upgrade itself into a controlled claim.

## Measurement Hygiene

Future execute mode must capture enough state to make bad runs rejectable.
The infra phase must define fields, validators, and refusal logic for:

- monotonic and wall-clock timestamps, with clock ambiguity caveats;
- host, boot id, NixOS generation, kernel, CPU governor, power profile, thermal
  state where available, GPU PCIe regime, and relevant service states;
- command, derivation/workload identity, git revision, dirty flag, environment
  digest, cache condition, and retry/attempt number;
- warmup/discard policy and explicit exclusion rules;
- exit status, cancellation, timeout, and partial-output state;
- background pressure windows: CPU, memory, IO PSI, service contention, and
  machine episodes.

A run with missing hygiene fields may still be useful for observation, but the
design assessor must state which support levels are impossible.

## Advanced Design And Instrumentation

The planner should use the simplest design that can answer the question, but it
must know the stronger designs when the candidate demands them.

Design capabilities:

- blocked randomization for derivation/package/cache/host strata;
- paired and crossover designs for before/after or treatment/control runs;
- Latin-square ordering when order, cache, or thermal carryover matters;
- factorial designs for multiple suspected factors;
- fractional-factorial designs only when aliases are declared;
- sequential designs only with predeclared interim looks and stopping rules.

Instrumentation bundles:

- `minimal`: manifest timestamps, command identity, exit status, telemetry
  overlap;
- `build_phase`: minimal plus Nix internal-json and derivation closure;
- `system_pressure`: minimal plus PSI, CPU, memory, IO, service/cgroup state;
- `microarchitectural`: selected `perf stat` counters where available;
- `trace`: bounded heavy tracing such as perf/eBPF only for targeted windows.

Heavy tracing is never default. Plans declare the bundle, expected overhead, and
which mechanism would remain unobserved without it.

Measurement-system handling:

- timer resolution and clock source are recorded;
- timeout/cancelled runs are censored outcomes, not silently dropped;
- warmup and carryover are modeled or explicitly excluded;
- repeated baseline runs estimate measurement noise;
- variance is decomposed by run, derivation, cache condition, and host regime
  when enough data exists.

## Reproducibility And Calibration

Scientific infrastructure must test the measurement system itself.

Required replay properties:

- generated plans are byte-stable for the same input and seed;
- estimate artifacts can be recomputed from promoted rows without reading
  narrative text;
- claim promotion is deterministic over the same rows, validator version, and
  support rules;
- artifact payloads include schema version, code revision, input artifact
  digests, source coverage bounds, and validator version.

Required calibration fixtures:

- null fixture: identical control/treatment distributions produce no promoted
  effect claim;
- known-effect fixture: injected slowdown recovers the expected direction and
  interval scale;
- broad-scan null fixture: many packages/stages/phases under no true effect
  keep false positives behind FDR/support gates;
- confounded fixture: naive association appears, but matched/adjusted or
  assumption checks prevent support upgrade;
- leakage fixture: future/post-treatment covariates improve apparent accuracy
  but are rejected by the feature-frame validator;
- broken-design fixture: missing randomization, missing cache proof, and
  changed workload mix each force insufficient support;
- placebo fixture: unrelated phase/package/window does not inherit the primary
  effect;
- missingness fixture: absent rows and out-of-coverage days are refused or
  caveated, never coerced to zero.

These fixtures are not substitutes for real campaigns. They prove that the
claim machinery refuses bad science before real data arrives.

## Implementation Tooling Contract

Use the repo's existing analytic stack before adding new dependencies:

- DuckDB for substrate joins, feature-frame construction, temporal windows,
  grouping, and coverage-aware scans;
- Polars for in-memory vectorized transforms where dataframe code is clearer
  than SQL;
- NumPy/SciPy for bootstrap, permutation tests, rank tests, robust summaries,
  confidence intervals, and numerical routines;
- hmmlearn only for regime/candidate segmentation, never for support-level
  causal claims.

Optional new dependencies are allowed only when they replace a fragile local
implementation and come with a named consumer, fixture, and support gate. Good
future candidates may include statsmodels for interrupted-time-series/event
study models, scikit-learn for clustering or propensity models, ruptures for
change-point detection, or lifelines for censored-duration analysis. Do not add
one merely to name an advanced method.

System instrumentation adapters should prefer stable local interfaces and
existing tools: Nix internal-json logs, `/proc/pressure`, `/proc/stat`, cgroup
and systemd state, journal timestamps, sysfs thermal/power/GPU state, and
`perf stat` when available. Heavy tracing through perf/eBPF is opt-in per plan.

## Nix Internal-JSON Infrastructure

Add parser and row model for Nix `--log-format internal-json`.

Required extracted fields:

- source log path;
- derivation id/path;
- event kind;
- phase name where available;
- timestamp;
- start/end/duration where derivable;
- run id / run group id linkage;
- parse caveats for missing or malformed events.

Required analytics:

- phase duration by derivation;
- cache-condition comparison by phase;
- slowest phase candidates;
- phase-level estimate input for controlled claims.

No live Nix execution is required for this infra phase; use fixtures.

## Candidate Generation

Candidates are a queue, not claims.

Candidate fields:

- stable id;
- project if known;
- metric;
- suspected factor;
- mechanism family when classifiable;
- discovery window and validation status;
- mining scan id and rank within the searched universe;
- priority score;
- summary;
- source artifacts and source ids;
- suggested benchmark manifest skeleton;
- caveats.

Candidate classes to support:

- command duration slower under pressure state;
- high p95/max stage duration;
- repeated slow test or package;
- failure concentration by stage/package/failure type;
- cohort with high variance or strong tail behavior;
- cohort whose matched controls are stable while the treated cohort changes;
- git revision boundary regression;
- NixOS generation boundary regression;
- hardware regime boundary regression;
- service-state boundary regression;
- machine-context anomaly cluster;
- lagged pressure exposure before a slowdown;
- mechanism signature match with falsifiers;
- cache-sensitive build phase;
- service/process contention;
- insufficient-data candidate where the right next action is instrumentation.

Ranking inputs:

- effect size;
- sample count and recurrence;
- validation strength;
- search-universe size and multiplicity burden;
- match/control quality;
- boundary plausibility;
- heterogeneity concentration;
- mechanism clarity and falsifiability;
- operational pain;
- controllability;
- benchmark cost;
- evidence completeness;
- whether the suspected factor maps to a concrete treatment.

Candidate ranking should optimize expected value of information, not raw
surprise. A large slowdown that cannot be controlled or reproduced may rank
below a smaller effect with cheap, clean experimental design.

Ranking must expose score components and a Pareto frontier. A single scalar
priority is acceptable for sorting, but the UI/MCP layer must preserve why a
candidate is high-value: effect size, recurrence, controllability, mechanism
clarity, control quality, operational pain, and experiment cost.

## Estimation And Claim Logic

Controlled estimates:

- mean delta;
- median delta;
- p95 delta when sample size permits;
- unit-aligned estimand output: run-level, phase-level, derivation-level,
  package-level, or test-level, matching the pre-analysis record;
- deterministic bootstrap confidence intervals;
- paired deltas when randomized plan has pairs;
- per-derivation and per-cache-condition interaction estimates;
- power/sample-size estimates for future reruns when intervals are too wide.

Use paired or blocked estimators whenever the design supplies pairs/blocks.
Do not collapse blocked randomized data into a single unpaired aggregate unless
the block effect is negligible and the claim says so.

Natural-experiment estimates:

- before/after or boundary comparison;
- matched workload grouping;
- event-study pre/post profile when repeated windows exist;
- synthetic-control comparison when several unaffected control cohorts exist;
- explicit non-randomization caveat;
- no controlled language.

Allowed natural-experiment methods:

- interrupted time series for clear temporal boundaries;
- event study with visible pre-trends and post-boundary profile;
- difference-in-differences when a comparable unaffected workload exists;
- synthetic control when a donor pool of unaffected workloads exists;
- regression discontinuity only when the boundary assignment rule is explicit;
- matched before/after comparisons when workload mix can be matched;
- change-point detection as candidate evidence, not causal proof.

Observational mining estimates:

- matched or stratified association summaries;
- discovery-window estimate plus held-out validation estimate when coverage
  permits;
- rolling-origin backtest for recurring patterns;
- propensity-score matching or weighting only as a balance tool, not a causal
  upgrade by itself;
- doubly robust estimates only when sample size and covariate coverage support
  both outcome and treatment models;
- causal discovery, Granger-style temporal precedence, invariant prediction,
  tree models, and feature importance only as candidate-generation inputs.

Confidence degradation factors:

- low sample count;
- nonzero exit status;
- dirty git checkout;
- missing telemetry samples;
- incomplete internal-json capture;
- missing cache-condition proof;
- pressure episodes overlapping windows;
- changed workload/derivation set;
- clock/window ambiguity.

Multiple comparisons must be controlled when ranking or claiming over many
packages, stages, phases, or metrics. Use false-discovery-rate control for
candidate screening and stricter family-wise or pre-registered metric handling
for claims.

Broad mining must record the full search universe. A top candidate without its
scan id, search-space denominator, filters, and multiplicity policy remains
exploratory even when its local effect size is large.

Claim generation must emit either:

- promoted `analysis_claim` with support level and evidence; or
- structured refusal explaining which support requirements failed.

Claim payloads must state the estimand, estimator, unit of analysis, sample
counts, confidence interval, primary metric, secondary metrics if inspected,
coverage bounds, exclusion counts, and sensitivity result. A claim missing any
of those remains insufficient even if the point estimate is large.

Non-controlled claim payloads must also include the assumption ledger and the
support ceiling implied by failed or untested assumptions.

## Reasoning Utilities

Future analysis needs reusable reasoning helpers, not only numerical summaries.

Required utilities:

- dataset-mining inventory builder that enumerates cohorts, boundaries,
  possible controls, and coverage gaps before candidate selection;
- mining-scan registry that records every metric/cohort/filter/method family
  searched and the denominator used for multiplicity control;
- feature-frame builder that enforces temporal leakage, missingness, and
  censoring rules before estimation;
- discovery/validation splitter that prevents broad scans from promoting their
  own discoveries without held-out or rolling-origin checks;
- mechanism classifier that maps candidates to expected signatures,
  discriminating measurements, falsifiers, and next instruments;
- confounder registry for machine attribution: cache state, derivation mix,
  package/test mix, host load, software revision, hardware regime, dirty git
  state, thermal/power regime, machine pressure episodes, and clock/window
  ambiguity;
- design assessor that maps a manifest/run group to support level and names the
  exact blockers;
- negative-control/placebo helpers where possible, such as unrelated packages,
  unrelated phases, or pre-boundary windows;
- sensitivity summary that reports how a conclusion changes after excluding
  failed runs, dirty trees, pressure-overlapped runs, or outlier phases;
- causal-model helper that flags missing adjustment variables, post-treatment
  adjustment, and unobserved confounding;
- assumption-ledger helper that records which assumptions were checked, failed,
  untestable, or left unchecked and how each one affects support level;
- claim-language guard that produces allowed phrasing for observation,
  candidate, natural experiment, controlled claim, and insufficient support.

These utilities should be importable by MCP tools, context packs, and dossier
generation so the same support judgment is reused everywhere.

## Method Catalog

Use concrete methods with explicit applicability gates.

| Method | Use For | Gate | Output |
|---|---|---|---|
| blocked randomization | controlled benchmark plans | known block keys | balanced run order |
| Latin square / crossover | order and carryover control | stable workload; declared carryover risk | balanced order plan |
| factorial design | several suspected factors | factors can be independently manipulated | main effects/interactions |
| fractional factorial | expensive factor scans | alias structure declared | screened factor candidates |
| paired bootstrap | paired benchmark runs | explicit pairs | CI for paired delta |
| stratified bootstrap | derivation/cache strata | non-empty strata | CI preserving strata |
| censored-time summary | timeout/cancelled runs | censoring recorded | honest timeout-aware estimate |
| rolling-origin backtest | recurring mined patterns | enough ordered windows | validation profile |
| permutation test | randomized treatment labels | exchangeable labels | exact/randomization p-value |
| Mann-Whitney / rank tests | non-normal exploratory comparisons | independent samples | candidate signal |
| robust regression | covariate-adjusted observational estimates | enough samples per covariate | adjusted estimate |
| propensity matching/weighting | observational balance checks | overlap and balance diagnostics pass | balanced comparison candidate |
| doubly robust estimate | observational association stress test | enough samples for treatment and outcome models | sensitivity estimate |
| interrupted time series | boundary regressions | stable pre/post series | natural-experiment estimate |
| event study | boundary shape and pre-trend check | repeated pre/post windows | pre/post profile |
| difference-in-differences | affected vs control workload | parallel-trend plausibility | natural-experiment estimate |
| synthetic control | one treated workload with donor pool | unaffected donor cohorts | natural-experiment estimate |
| invariant prediction / causal discovery | structure proposal | multiple environments/windows | candidate causal graph |
| PELT/CUSUM change points | unknown regression boundaries | time-ordered samples | candidate boundary |
| hierarchical shrinkage | many tests/packages/phases | repeated related groups | stabilized ranking |
| FDR correction | broad candidate scans | many simultaneous tests | q-values/ranked candidates |
| sensitivity analysis | support robustness | exclusion dimensions exist | caveat/robustness summary |

Avoid method theater:

- do not compute p-values for uncontrolled, convenience samples and present
  them as causal support;
- do not use a model that has more degrees of freedom than the data can support;
- do not average away package, derivation, cache, or host blocks when those
  blocks explain the variation;
- do not report precision beyond measurement resolution;
- do not claim “no effect” from an underpowered run; emit insufficient support.
- do not adjust for post-treatment variables as if they were confounders;
- do not present adaptive reruns as confirmatory unless interim looks and
  stopping rules were predeclared.
- do not treat causal discovery, feature importance, SHAP-style explanations,
  or Granger precedence as causal support without an identification design.

## Evidence Graph

Required node kinds:

- existing: `machine_work_observation`, `machine_episode`,
  `machine_context_window`, `machine_baseline`, `machine_experiment_claim`;
- add: `machine_mining_scan`, `machine_observation_cohort`,
  `machine_boundary_candidate`, `machine_analysis_feature_frame`,
  `machine_matched_comparison`,
  `machine_discovery_validation_split`, `machine_negative_control`,
  `machine_mechanism_hypothesis`, `machine_assumption_check`,
  `machine_instrumentation_gap`, `machine_attribution_candidate`,
  `machine_benchmark_plan`, `machine_benchmark_run`,
  `machine_benchmark_phase`, `machine_benchmark_estimate`,
  `machine_attribution_claim`, `machine_attribution_refusal`.

Required edge kinds:

- `candidate_from_observation`;
- `candidate_from_mining_scan`;
- `candidate_from_cohort`;
- `candidate_from_boundary`;
- `candidate_uses_feature_frame`;
- `candidate_validated_by_split`;
- `comparison_matches_cohorts`;
- `negative_control_checks_candidate`;
- `mechanism_explains_candidate`;
- `assumption_check_limits_claim`;
- `instrumentation_gap_blocks_mechanism`;
- `candidate_from_artifact`;
- `plan_investigates_candidate`;
- `run_in_plan`;
- `phase_in_run`;
- `estimate_summarizes_runs`;
- `claim_supported_by_estimate`;
- `refusal_resolves_candidate`;
- `claim_resolves_candidate`;
- `work_observation_has_stage`;
- `work_observation_has_test`;
- `run_overlaps_machine_episode`;
- `run_overlaps_telemetry_window`.

Graph payloads must stay compact; large logs are provenance paths.

## MCP Surface

Existing tools cover basic work observations and candidate artifact summary.

Required additional tools:

- `machine_dataset_inventory(project?, start?, end?)`;
- `machine_mining_scans(project?, metric?, method?)`;
- `machine_observation_cohorts(dimension?, project?, limit?)`;
- `machine_feature_frames(frame_id?, unit_type?, project?)`;
- `machine_discovery_validation_splits(candidate_id?, project?)`;
- `machine_boundary_candidates(boundary_type?, project?, limit?)`;
- `machine_matched_comparisons(candidate_id?, boundary_id?)`;
- `machine_negative_controls(candidate_id?, boundary_id?)`;
- `machine_mechanism_hypotheses(candidate_id?, family?)`;
- `machine_assumption_checks(claim_id? candidate_id?)`;
- `machine_instrumentation_gaps(project?, source?)`;
- `machine_attribution_candidate_details(candidate_id)`;
- `machine_benchmark_readiness(manifest_path | payload)`;
- `machine_benchmark_plan_template(candidate_id)`;
- `machine_benchmark_plans(run_group_id?, candidate_id?)`;
- `machine_benchmark_runs(run_group_id?, workload?)`;
- `machine_benchmark_phases(run_id?, derivation?, phase?)`;
- `machine_benchmark_estimates(run_group_id?, metric?)`;
- `machine_attribution_claims(support_level?, project?, metric?)`;
- `machine_claim_evidence(claim_id)`;
- `machine_work_slow_tests(package?, project?, limit?)`;
- `machine_work_stage_daily(stage_name?, project?)`;
- `machine_work_failures(project?, package?, stage?)`;
- `machine_refresh_health()`.

MCP rules:

- read-only by default;
- JSON-safe output;
- explicit `status: missing` for optional absent artifacts;
- no hidden mutation or benchmark execution through ordinary MCP tools.

## Current-State, Dossier, And Narrative Surfaces

Current-state packs must show:

- top slow stages/tests/packages;
- top candidates;
- benchmark readiness gaps;
- latest controlled/natural/observational/insufficient claim counts;
- whether Polylogue was skipped/degraded;
- clear caveat that candidates are not claims.

Dossiers must preserve stage labels:

- observation;
- candidate;
- plan;
- run;
- estimate;
- claim/refusal.

Any generated prose about machine causality must cite support level and backing
artifact or graph node.

## Refresh Architecture

Current problem: `substrate_promote` is too broad for machine-attribution
iteration and pulls unrelated personal sources.

Required split:

- commits/files/symbols;
- PR reviews;
- work observations;
- machine telemetry;
- machine experiment manifests;
- personal daily products;
- evidence graph;
- AI work events.

Required refresh features:

- source selection from CLI;
- dependency-closed `--up-to` still works;
- full refresh remains the default;
- machine attribution refresh can avoid unrelated personal-source promotion;
- refresh timing artifact records per-step time, row counts, source statuses,
  and degraded/skipped reasons;
- Polylogue readiness is probed once per refresh and cached.

Performance targets:

- work-observation-only promote under 30s for current window;
- candidate refresh under 2m once substrate exists;
- `machine_context_windows` under 60s for one month;
- no repeated Polylogue product probes after degraded readiness is known.

Likely implementation moves:

- substrate-backed workload windows;
- precomputed parent/child work joins;
- materialized terminal/git/deep-work window artifacts;
- source-selected substrate promotion;
- explicit indexes/views for work observation parent/child queries.

## Analytics Surfaces

The infra phase must provide reusable analytics, not just raw tables:

- cohort inventory and coverage summaries;
- mining scan denominator and multiplicity summaries;
- feature-frame provenance and leakage-check summaries;
- discovery/validation and rolling-origin summaries;
- boundary inventory and pre/post coverage summaries;
- matched comparison and balance summaries;
- negative-control/placebo summaries;
- mechanism-family signature summaries;
- assumption-ledger summaries;
- lagged exposure summaries;
- anomaly-cluster summaries;
- daily command/project duration and failure summaries;
- stage duration trend and outlier summaries;
- package/test duration and failure summaries;
- failure taxonomy;
- Nix phase timing summaries;
- cache-condition comparison summaries;
- pressure/context overlap summaries;
- boundary comparison summaries for natural experiments;
- candidate ranking;
- controlled estimate summaries;
- power/sample-size guidance;
- sensitivity and negative-control summaries;
- claim/refusal summaries.

Each analytic should be available through at least one deterministic artifact or
substrate reader, and high-value analytics should also be MCP-queryable.

## Test Matrix

Required coverage:

| Area | Tests |
|---|---|
| Manifest readiness | accepts full contract; rejects loose hints |
| Planner | deterministic randomized order; generated manifests validate; DOE variants carry support ceilings |
| Internal-json parser | phase extraction; malformed-event caveats |
| Work joins | invocation-stage-test joins; non-timed package caveat |
| Dataset mining | scan registry; cohort inventory; boundary inventory; discovery/validation split; matched controls; gaps |
| Feature frames | temporal leakage rejection; missingness/censoring metadata |
| Candidate queue | ranking from mined products; skeleton manifest; caveats |
| Estimates | deterministic bootstrap; paired/grouped estimates |
| Reasoning | support-level assessor; confounder/sensitivity helpers; causal-model helper; mechanism helper; assumption ledger |
| Scientific validity | self-prompt artifact; pre-analysis record; estimand/estimator separation; coverage propagation; falsification/refusal reasons |
| Instrumentation | bundle validation; heavy-trace overhead/caveat handling |
| Measurement system | timer resolution; baseline repeatability; censored timeout handling |
| Calibration | null, known-effect, broken-design, placebo, and missingness fixtures |
| Claim promotion | valid controlled claim; invalid manifest refusal |
| Graph | candidate/plan/run/estimate/claim/refusal nodes and edges |
| MCP | registration; missing-artifact status; fixture reads |
| Refresh | DAG order; source selection; Polylogue-degraded success path |
| Status/context | machine sections include observations, candidates, claims |

## Operator Workflow

1. **Observe**: refresh work/machine substrates and artifacts.
2. **Select**: inspect candidates through MCP/current-state.
3. **Plan**: generate dry-run benchmark plan/manifests.
4. **Execute**: future operator step, out of current scope.
5. **Ingest**: promote run manifests, logs, telemetry overlaps.
6. **Estimate**: compute effect sizes and intervals.
7. **Claim/refuse**: promote support-level claim or refusal.
8. **Explain**: inspect graph/MCP/dossier evidence chain.

## Acceptance Criteria

Infra-complete requires all of these:

- work observations have parent/child analytics and MCP readers;
- extant dataset mining produces cohorts, boundaries, matched comparisons,
  mining scan registries, discovery/validation splits, negative controls,
  anomaly clusters, lagged exposure summaries, and instrumentation gaps;
- feature frames enforce exposure/outcome separation, leakage rejection,
  missingness flags, and censored rows;
- controlled benchmark plan generation exists and validates manifests;
- benchmark plans persist pre-analysis records, causal models, DOE support
  ceilings, instrumentation bundles, and mandatory plan/run linkage;
- Nix internal-json logs have parser, row model, fixtures, and summaries;
- attribution candidates cover cohorts, work, test, failure, boundary,
  pressure, cache, anomaly, lagged-exposure, and instrumentation-gap cases;
- controlled/natural/observational/insufficient claim promotion exists;
- broad mined candidates carry scan denominators and multiplicity policy;
- exploratory mined patterns do not promote unless validation, controls, or
  explicit support ceilings justify them;
- non-controlled claims include assumption ledgers and support ceilings;
- graph includes candidate, plan, run, phase, estimate, claim, and refusal nodes
  with evidence edges;
- MCP exposes dataset inventory, mining scans, cohorts, feature frames,
  boundaries, matched comparisons, discovery/validation splits, negative
  controls, mechanism hypotheses, assumption checks, instrumentation gaps,
  candidates, readiness, plans, runs, phases, estimates, claims, slow
  tests/stages, failures, and refresh health;
- current-state/status/dossier surfaces show the full ladder;
- self-prompt artifacts exist for major implementation phases and rejected
  method-theater ideas are recorded;
- refresh can build candidates and claim inputs without Polylogue repair;
- source-selected refresh avoids unrelated personal-source promotion;
- replay/calibration fixtures prove null, known-effect, broken-design,
  placebo, and missingness behavior;
- focused tests cover every row in the test matrix.

Empirical completion is separate:

- run at least one controlled benchmark campaign;
- ingest its manifests/logs/telemetry;
- emit a controlled claim or an explicit insufficient-support refusal.

## Work Packages

Implement in this order. The order follows the dataflow, so each package
creates a usable substrate for the next one.

1. **Refresh And Joins Foundation**: source-selected substrate steps, refresh
   timing artifact, work parent/child joins, workload windows, slow tests,
   failures, stage daily summaries.
2. **Feature Frame Spine**: analysis-frame schema, exposure/outcome windows,
   missingness/censoring fields, leakage validator, coverage propagation.
3. **Mining Spine**: scan registries, cohort inventory, boundary inventory,
   discovery/validation splits, lagged exposures, anomaly clusters, coverage
   and search-denominator summaries.
4. **Comparison Spine**: matched controls, balance diagnostics, negative
   controls/placebos, mechanism hypotheses, assumption checks, instrumentation
   gaps.
5. **Candidate Queue**: candidates from mined products, score components,
   Pareto frontier, support ceilings, benchmark skeletons, refusal candidates.
6. **Estimation And Claim Core**: observational, natural-experiment, and
   controlled estimators; confidence intervals; assumption ledgers;
   controlled/natural/observational/insufficient claim/refusal promotion.
7. **Benchmark Planning Path**: dry-run plan generator, pre-analysis record,
   causal model, DOE variants, instrumentation bundles, manifest writer, CLI,
   docs.
8. **Internal JSON And Instrumentation**: Nix internal-json parser, phase row
   model, derivation closure/caching metadata, bundle validators, overhead
   caveats, censored timeout handling, measurement-noise summaries.
9. **Graph Surface**: scan/cohort/boundary/feature-frame/validation/comparison/
   control/mechanism/assumption/gap plus candidate/plan/run/phase/estimate/
   claim/refusal nodes and edges.
10. **MCP Surface**: inventory/scan/cohort/feature-frame/boundary/comparison/
   validation/control/mechanism/assumption/gap plus
   detail/template/readiness/plans/runs/phases/claims tools.
11. **Context And Dossier Surface**: support ladder, evidence chain,
   assumption ledger, refusal summaries, source readiness, Polylogue-degraded
   handling.
12. **Calibration And Reproducibility**: replay metadata, null/known-effect/
   broad-scan/confounded/leakage/broken-design/placebo/missingness fixtures.
13. **Performance Pass**: machine context speed, degraded-source caching,
   substrate workload-window readers, scan materialization costs.

If these work packages are implemented and verified, further substantial work
for this slice is empirical execution and interpretation, not infrastructure.
