# Intelligent Materialization and Cross-Repo Simplification Plan

## Summary

The current system already contains most of the right raw ingredients:
source contracts, materializers, a DuckDB substrate, MCP readers, machine
telemetry, keylog capture, operator-daily joins, and Sinnix resource controls.
The old queue/control-plane refresh surface has been retired from normal
operation. The remaining simplification work is to make every materialized
product cheap to inspect, incrementally update when owned locally, and stay
readable without exposing materialization mechanics as an operator workflow.

The target is simpler:

1. Sources are either live, locally materializable, derived, coverage-bound, or
   manual.
2. Locally materializable and derived products are kept current by a single
   cheap incremental materialization path.
3. Reads call `ensure_materialized(...)` and then read the product.
4. If the product is already current, `ensure_materialized(...)` is a cheap
   no-op.
5. If the product cannot be advanced locally, the read returns a typed coverage
   or blocked status. It does not create normal queue work.
6. Queueing is not part of Lynchpin's normal transparent-refresh mechanism; any
   future queue belongs to an explicit external worker/ledger with a real
   consumer.
7. Cross-repo resource orchestration belongs outside Lynchpin. Sinnix owns
   systemd/cgroup policy, capture daemons own capture, and a future devloop-lite
   owns run/proof/cost ledgers. Lynchpin reads and analyzes those facts.

The ambition is not less capability. It is less machinery: every current useful
thing should become either a materialized product, a source-owned capture stream,
or a cross-repo ledger fact that Lynchpin can analyze.

## Current Inventory

### Lynchpin Data Surfaces

- `SourceContract` registry: 35 declared datasets/stages.
  - MaterializationMode split observed in the registry: 8 live, 7 local, 4 derived,
    13 coverage-bound, 3 manual.
  - Collection models: 13 continuous, 13 event exports, 4 derived, 2 metadata,
    2 historical, 1 stage.
- Materialization registry: 17 executable materializers.
  - Local/continuous: webhistory, activitywatch, activity_content, atuin,
    machine, irc.
  - Local/imported export products: google_takeout, spotify, reddit,
    facebook_messenger, communications, raindrop, browser_bookmarks, arbtt.
  - Derived products: spotify_daily, personal_daily_signals.
- Substrate: DuckDB-derived read store with source status, run steps, machine
  tables, evidence graph tables, work observations, and MCP SELECT access.
- Analysis artifacts: machine-analysis DAG products, operator-daily products,
  retrospective readiness, current-state/context-pack artifacts.
- MCP surface: broad, with materialization status, capability, artifact,
  machine, substrate, operator, source, and personal tools. Queue tools are no
  longer registered; runtime ledger tools are diagnostic/provenance-only.

### Refresh/Freshness Surfaces

- `lynchpin.core.freshness` currently owns only a diagnostic receipt/dependency
  ledger and compact materialization status helpers. It does not own a queue,
  worker, policy system, or execution bridge.
- `lynchpin.analysis.core.materialization_intelligence` currently owns:
  - advisory DAG step plans;
  - per-step skip/run/inspect decisions;
  - cost and mode labels;
  - executable-step filtering so inspect-only rows are not run.
- Sinnix keeps broad materialization scheduling/resource policy. The old
  frequent Lynchpin worker timer is no longer installed by default.

The final model remains product readiness and incremental materialization, not
refresh telemetry.

### Machine Observability

- Machine telemetry capture and substrate promotion exist.
- Machine analysis has a large artifact/DAG stack: episodes, context windows,
  below analysis, benchmark plans, experiment claims, comparisons, support
  assessments, assumptions, instrumentation gaps, and readiness reports.
- Machine MCP has grown broad: daily metrics, context metrics, work mechanics,
  slow tests, failure summaries, benchmark queues, attribution candidates, claim
  evidence, service state, bufferbloat, gap summaries, and more.

The capability is valuable, but it should be organized around materialized
products and a small number of query surfaces. Machine analysis should not
teach every intermediate artifact as a first-class operator concept.

### Keylog / Keybind Analysis

- `scribe-tap` keylog captures exist under `/realm/data/captures/keylog`.
- Lynchpin has keylog analysis capable of:
  - parsing key events;
  - joining configured Hyprland binds;
  - estimating keybind frequency over time;
  - producing higher-level metadata and text-shape analysis.
- Current keybind inference is limited because capture lacks full modifier
  state and key release/chord lifecycle. Exact Hyprland keybind analytics should
  be fixed at capture time, not by piling heuristics into Lynchpin.
- Text-level analysis should remain possible. Do not encode a fake policy that
  forbids it. The clean boundary is product separation: keybind analytics,
  text-shape analytics, and text-content analytics are distinct products with
  explicit consumers.

### Adjacent Repos and System Boundaries

- Sinnix:
  - should own systemd units, timers, slices, CPU/IO weights, Nix packaging,
    and host policy;
  - should not know detailed Lynchpin freshness semantics.
- Sinex:
  - should keep Sinex-specific `xtask` UX and domain commands;
  - should not own general machine-wide resource accounting.
- Future devloop-lite:
  - should own run/CI/job ledger, artifact collection, proof freshness over
    real executed commands, failure packets, remote proof routing, branch/PR/CI
    outcome ingestion, and cost/resource accounting;
  - should not become a custom proof ontology or workflow engine.
- Polylogue:
  - should own chat/session/work-event materialization and daemon reliability;
  - Lynchpin should consume its products and report degraded readiness when
    they are missing, not repair them through a Lynchpin refresh queue.
- Capture daemons:
  - ActivityWatch, Atuin, scribe-tap, polylogued, machine telemetry, webhistory
    capture, and similar sources should continuously append or maintain their
    own raw/live stores.
  - Lynchpin should materialize from them; it should not pretend to be their
    scheduler.
- Noctalia/Waybar:
  - should consume one compact status product, not a queue dashboard.

## Target Architecture

### Core Rule

Replace "freshness as a user-facing control plane" with "materialization as a
read precondition."

Every read path that depends on a product should do:

```python
result = ensure_materialized("product_name", window=window, budget="inline")
if result.status in {"ready", "updated"}:
    return read_product(...)
return blocked_or_coverage_payload(result)
```

`ensure_materialized(...)` must be cheap enough to call freely. It may inspect
source high-water marks, manifests, mtimes, row counts, table max timestamps,
schema versions, and prior materialization metadata. It must not run expensive
full scans just to decide whether work is needed.

### Public Interface

Add a new materialization interface and make it the center of the system:

```python
MaterializationStatus = Literal[
    "ready",
    "updated",
    "blocked",
    "failed",
    "coverage_bound",
    "manual",
]

MaterializationBudget = Literal["inline", "background", "manual"]

@dataclass(frozen=True)
class MaterializationResult:
    name: str
    status: MaterializationStatus
    changed: bool
    reason: str
    elapsed_ms: int
    product_paths: tuple[Path, ...]
    source_high_water: dict[str, str | int | float | None]
    coverage: dict[str, str | None]
    diagnostics: tuple[str, ...] = ()

def ensure_materialized(
    name: str,
    *,
    window: tuple[date, date] | None = None,
    budget: MaterializationBudget = "inline",
    force: bool = False,
) -> MaterializationResult: ...
```

Compatibility rule: existing `audit_materialization()` and
`plan_materializations()` remain as reporting helpers, but the normal read path
uses `ensure_materialized(...)`.

### Product Manifests

Each materializer writes or updates a product manifest containing:

- product name and schema version;
- product paths and substrate tables;
- source authority and source high-water marks;
- source fingerprints cheap enough to re-check;
- row count, first/last logical date, first/last observed timestamp;
- last successful materialization time;
- materializer version;
- failure diagnostics from the last run, if any.

Manifests replace freshness receipts for normal operation. Receipts can remain
only as debug history during migration and then be deleted or archived.

### Source Classes

Use source classes to drive behavior, not ad hoc queue decisions:

- `live`: read directly or from source-owned live DB/log; no Lynchpin refresh.
- `local`: `ensure_materialized` may run the local materializer inline when
  cheap and safe.
- `derived`: `ensure_materialized` may rebuild from already-materialized local
  inputs.
- `coverage_bound`: report coverage and missingness; never imply local refresh
  can extend the source.
- `manual`: report the required external action; do not queue routine work.

### Queue Policy

Queueing is not the transparent-refresh mechanism.

Introduce any future queue only as an explicit external worker/ledger for
exceptional work:

- explicit user-requested heavy rebuilds;
- failed jobs needing retry;
- manually scheduled background work;
- external devloop/Sinnix work packets once a real consumer exists.

Normal reads must not enqueue. A system with only a little workload should just
do the little work inline.

### MCP and CLI Surface

Collapse the normal operator/API surface to:

- `materialization_status`: product readiness, coverage, last success, and
  blocked/manual reasons;
- `analysis_artifact_inventory` / `read_analysis_artifact`: artifact discovery
  and reads, internally calling `ensure_materialized` for products they own;
- `mcp_capability_matrix`: capability/readiness map over product status;
- one compact `lynchpin status --json` CLI for Noctalia/Waybar/operator panels.

Removed from normal MCP/operator use:

- `diagnostic_queue`;
- `diagnostic_queue_summary`;
- `diagnostic_queue_aging`;
- `diagnostic_queue_worker_runs`;
- `diagnostic_queue_worker_once`;
- legacy queue-backed panel status.

Read-only diagnostic ledger tools remain available for provenance/debugging,
but they no longer carry queue execution state in the main status story.

## Implementation Plan

### Phase 1: Put Materialization at the Center

- Add `ensure_materialized(...)` and `MaterializationResult` near
  `lynchpin.materialization`.
- Teach it to use the existing materializer registry first.
- Support the five source classes from `SourceContract.materialization_mode`.
- For local/derived products:
  - check manifest/high-water state;
  - no-op if unchanged;
  - run the materializer inline if missing/expired and budget permits;
  - return `blocked` if budget forbids required work.
- For live/coverage-bound/manual products:
  - return status and reason without queue writes.
- Update `audit_materialization()` to report manifest/high-water fields when
  available.
- Keep `MaterializationExecutor` only as a binding from source contract to an
  owned materializer. Command/argv executors are not part of the model.

Acceptance:

- Calling `ensure_materialized("machine")` or another local materializer twice
  runs at most once when inputs are unchanged.
- Calling it for coverage-bound exports reports coverage/manual status and does
  not enqueue.
- Calling it for live sources reports live status and does not enqueue.

### Phase 2: Remove Normal Queue Semantics

- Stop all ordinary read paths from calling `ensure_artifact_fresh(...)` in a
  way that enqueues.
- Replace artifact freshness checks in MCP artifact reads with
  `ensure_materialized(...)` where a product owner is known.
- Delete or quarantine advisory receipt recording for DAG planning. The current
  materialization planner is side-effect-free and has no receipt conversion
  hook.
- Make the analysis DAG skip logic use manifests/product status directly.
- Keep only diagnostic ledger history for provenance/debug; do not keep queue
  tables or queue-control APIs in Lynchpin.

Acceptance:

- End-to-end MCP artifact reads do not create queue/control-plane work.
- `python -m lynchpin.analysis materialize --dry-run` or equivalent reports product
  actions from materialization manifests, not freshness receipts.
- Existing useful "why unavailable?" messages survive as materialization
  reasons.

### Phase 3: Simplify Sinnix Integration

- Remove or default-disable `sinnix.services.lynchpin.workerTimer`.
- Keep one daily/background `lynchpin-materialize` timer for broad materialization
  if it remains useful, but make it run a materialization command rather than a
  diagnostic queue-worker command.
- Keep resource policy in Sinnix: background slice, CPU/IO weights, timeout,
  and randomized daily scheduling.
- Update the Sinnix service test to assert the simplified boundary:
  - daily refresh/materialize service exists when enabled;
  - no frequent cheap worker timer is enabled by default;
  - any heavy/background refresh runs in the intended slice.

Acceptance:

- No default five-minute Lynchpin worker exists.
- The system still has a broad background catch-up path.
- Status panels are not queue panels.

### Phase 4: Consolidate MCP Tooling

- Keep the underlying machine artifacts and analysis capabilities.
- Replace broad intermediate machine MCP exposure with grouped product readers:
  - status/readiness;
  - metrics and pressure;
  - work/test performance;
  - attribution/claims;
  - benchmark plans/execution;
  - diagnostics/gaps.
- Preserve specialized tools only where they answer distinct questions better
  than a grouped product reader.
- Move freshness tools behind debug naming or remove them after migration.
- Add `keylog_daily`, `keybind_usage`, and optional text-analysis MCP surfaces
  once the products are materialized.

Acceptance:

- The MCP capability matrix remains complete but smaller and easier to reason
  about.
- An agent can still answer all current machine observability questions.
- The normal status story references products and coverage, not queue age.

### Phase 5: Make Keylog Intelligent

- Materialize keylog products:
  - key events by logical day;
  - inferred Hyprland keybind usage by bind/action/time;
  - key frequency and chord frequency;
  - text-shape metrics;
  - optional text-content products if useful.
- Add capture improvements to `scribe-tap`:
  - modifier state on every key event;
  - key release events;
  - stable device/layout metadata;
  - event IDs or monotonic sequence numbers.
- After capture improves, replace keybind inference heuristics with exact
  chord/bind matching.

Acceptance:

- "Which Hyprland binds do I actually use over time?" is answerable from a
  materialized product.
- Text-level analysis is possible as a separate explicit product.
- Existing weaker inference is labeled as inference only until capture supports
  exact bind matching.

### Phase 6: Make Machine Observability Intelligent

- Treat machine analysis as a product family, not a pile of independent tools.
- Define product groups:
  - raw telemetry rollups;
  - pressure episodes;
  - command/test performance;
  - workflow mechanics;
  - causal candidates and support assessments;
  - benchmark plan/execution products;
  - instrumentation gaps.
- Ensure each product has manifest/high-water metadata and can be cheaply
  skipped.
- Use machine pressure only to decide whether to defer non-inline heavy work.
  Do not make pressure guard part of every cheap read.

Acceptance:

- A machine status read can explain what is happening now, recent pressure, and
  likely causes from materialized facts.
- Running the machine DAG twice with unchanged inputs is cheap.
- Intermediate artifact names do not leak into the primary user model.

### Phase 7: Cross-Repo Devloop-Lite Boundary

- Do not extract a broad devloop workflow engine yet.
- Define the shared devloop-lite repo/API narrowly:
  - run/job ledger;
  - artifact/log capture;
  - proof freshness over real executed commands;
  - duplicate proof suppression;
  - failure packets;
  - remote proof routing;
  - cost/resource accounting;
  - branch/PR/CI outcome ingestion.
- Leave Sinex `xtask` in Sinex, but hollow generic run/resource/proof behavior
  into devloop-lite when it is mature enough.
- Lynchpin consumes devloop-lite ledgers as another source and correlates them
  with machine pressure, AI sessions, commits, and operator activity.
- Polylogue records agent/session/workflow context around those runs.
- Sinnix/CI provide heavy compute and resource isolation.

Acceptance:

- Sinex-specific commands remain in Sinex.
- Generic "what ran, what did it cost, what passed, what artifact/log belongs to
  it?" data is not Sinex-specific.
- Lynchpin can analyze devloop behavior without owning orchestration.

### Phase 8: Status Product for Desktop UI

- Add one compact `lynchpin status --json` output for Noctalia/Waybar:
  - overall state: `ok`, `degraded`, `blocked`, `running`;
  - last successful broad materialization;
  - product groups with expired/blocked counts;
  - machine pressure summary;
  - newest actionable blocker;
  - no normal queue age unless an exceptional queue remains non-empty.
- Desktop UI should be a thin consumer of this product.

Acceptance:

- The desktop panel answers "is the analysis substrate healthy?" without
  teaching refresh internals.
- Clicking/drilling can open materialization status or machine details, not a
  queue-control dashboard.

## Removal / Simplification Candidates

Remove or fold these once their useful information is represented by product
manifests and materialization status:

- DAG advisory refresh receipts.
- Per-read freshness receipts for normal successful/no-op reads.
- Dependency receipt edges for ordinary artifact reads.
- Frequent cheap worker timer.
- Queue aging/panel surfaces as primary operator UX.
- Cost/mode labels on every normal read path.
- Standalone refresh-intelligence plans that merely restate whether artifacts
  exist or are old.

Keep these capabilities, but simplify their surface:

- Machine analysis products.
- Materialization audit.
- Source coverage/readiness.
- Artifact inventory/read.
- Capability matrix.
- Keylog/keybind analytics.
- Cross-source operator daily and retrospective products.

## Implemented Slices

- `ensure_materialized(...)` is now the normal direct API for locally
  materializable products; local materializers run inline under an inline
  budget, and coverage-bound/manual/live sources return typed status instead of
  routine queue work.
- Artifact reads and dashboard/current-state reads now return materialization
  status rather than freshness receipts.
- Machine DAG runs now write materialization reports and the machine command is
  `materialize-machine`; the old `refresh-machine` compatibility spelling was
  removed.
- The machine report product is `machine_analysis_materialization_report.json`;
  machine MCP/readiness/context-pack/status readers consume that product.
- `lynchpin status --json` now emits a compact
  `lynchpin_materialization_status` payload with product and machine state only.
  Queue diagnostics are opt-in through `observability-status` and explicit
  diagnostic commands.
- MCP substrate snapshot selectors no longer write freshness receipts during
  normal reads. The old `*_fresh` helper names were removed; selector helpers
  now use materialized refresh-id naming while still reading the real DuckDB
  `refresh_id` schema field.
- DAG materialization planning is now side-effect-free. The old
  `receipts_for_plan(...)` and freshness-policy conversion hooks were removed
  from the planner rather than retained as dormant receipt-writing machinery.
- The controlled-benchmark execution product is now an execution handoff, not
  an execution queue: `machine_benchmark_execution_handoff.json`,
  `machine-benchmark-handoff`, MCP `machine_benchmark_execution_handoff`, and
  graph evidence kind/relation names all use handoff terminology.
- The bounded `below` export planner is now a handoff product rather than a
  queue product: `machine_below_export_handoff.json`, MCP
  `machine_below_export_handoff`, graph evidence kind/relation names, status
  fields, and context-pack text use handoff/planned-window terminology.
- Legacy freshness/queue surfaces are now explicitly described as diagnostic
  and exceptional in the core module docstring, CLI help, MCP tool docstrings,
  and source-contract executor docs. Command names remain for compatibility, but
  new operators are no longer taught that the queue is the normal refresh model.
- `SourceContract` now carries `materialization_target`; the MCP capability
  matrix exposes the same name, and the old `freshness_target` field is gone
  from the contract model.
- `OperatorDay` no longer carries freshness/refresh-ledger receipt or worker
  counters. Ledger diagnostics remain in explicit hidden debug commands instead
  of being baked into the normal cross-source daily matrix.
- Materialization report helpers no longer accept or emit freshness receipts,
  freshness dependency edges, queued refreshes, or old refresh-report
  compatibility aliases.
- Sinnix no longer installs a frequent `lynchpin-refresh-worker` service/timer
  or `workerTimer` option. `sinnix-prime` keeps the daily
  `lynchpin-materialize` materialization DAG timer via `materializationTimer`,
  while direct Nix evaluation and the host observability policy check assert the
  worker timer is absent.
- The top-level analysis DAG command is now `python -m lynchpin.analysis
  materialize`, with current-state materialization under
  `materialize-current-state`; the old `refresh`/`refresh-current-state`
  command names are no longer registered.
- Legacy `freshness-*` CLI command names were removed. Queue inspection/drain
  hooks were later removed entirely; the remaining explicit `diagnostic-*` CLI
  names expose only ledger receipts/dependency edges and materialization status
  while the normal CLI advertises materialization/status commands.
- Machine attribution candidates are now described as a ranked candidate set,
  not a candidate queue; benchmark planning help points at attribution
  candidates rather than a queue.
- Public source contracts, materialization audit rows, MCP capability/readiness
  payloads, and coverage reports now expose `materialization_hint` instead of
  `refresh_hint`. The hidden diagnostic freshness queue schema/tests were
  retired with the queue implementation.
- Public source contracts and the MCP capability matrix now expose
  `materialization_executor`/`MaterializationExecutor` instead of
  `refresh_executor`/`RefreshExecutor`; the diagnostic freshness policy/queue
  bridge was removed.
- Current-state substrate rebuilding now uses the public
  `--materialize-substrate` flag and `materialize_substrate` parameter. The old
  refresh-named substrate flag was removed from current-state and snapshot
  forwarding surfaces.
- Normal analysis-artifact reads now use
  `load_materialized_analysis_artifact(...)` instead of the old
  `load_analysis_artifact_fresh(...)` compatibility helper. Ignored
  `max_age_seconds`/`caller` freshness parameters were removed from those
  machine/dashboard read paths.
- The MCP compatibility alias `machine_refresh_health` was removed; machine
  readiness is exposed through `machine_materialization_health` only.
- The `compact_observability_status(...)` compatibility alias and
  queue-inclusive materialization-status mode were removed. Compact status now
  reports substrate/materialization state only; queue diagnostics no longer ride
  on the live observability payload.
- MCP substrate-selector helpers now use materialized naming:
  `latest_materialized_refresh_id`, `best_materialized_refresh_id`, and
  `require_best_materialized_refresh_id`. The old `*_fresh` compatibility names
  were removed while preserving the real DuckDB `refresh_id` schema field.
- Selected machine benchmark execution now uses `materialize_after`,
  `materialization_commands`, and `materialization_exit_codes`, with the CLI
  flag `--materialize-after`. The old benchmark `--refresh` post-processing
  flag and result field names were removed.
- The analysis DAG implementation module is now `lynchpin.analysis.materialize`
  rather than `lynchpin.analysis.refresh`; CLI and tests import the
  materialization-named module directly.
- Advisory DAG planning now lives in
  `lynchpin.analysis.core.materialization_intelligence` with
  `MaterializationStepPolicy`, `MaterializationPlanRow`, and
  `materialization_plan_for_dag`. The old refresh-intelligence module and
  symbols were removed.
- DAG run report helpers now live in
  `lynchpin.analysis.core.materialization_report`. The old refresh-report
  module/test names and `refresh_plan` compatibility parameter were removed.
- MCP runtime ledger tools now use `diagnostic_*` names instead of
  `freshness_*` names. Queue tools are no longer registered; the core freshness
  module remains only as the small diagnostic receipt/dependency ledger.
- Hidden CLI ledger commands now use explicit `diagnostic-*` command names. The
  old hidden `freshness-*` command names and queue-control commands were removed
  rather than kept as compatibility aliases.
- DAG CLI execution plumbing now uses `explain_materialization`,
  `.lynchpin/log/materialization.log`, and missing/expired language for
  incremental machine materialization.
- Materialization planner reasons now use materialization age-horizon language
  instead of stale/fresh terminology.
- MCP selector utilities no longer expose old `latest_refresh_id`,
  `best_refresh_id`, or `require_best_refresh_id` helper APIs. Materialized
  selector helpers own the implementation while still reading the real
  `refresh_id` substrate key.
- Ecosystem dashboard serving now uses `materialize_on_request` and
  `--materialize-on-request`; the old `refresh_on_request` /
  `--refresh-on-request` normal dashboard surface was removed.
- Substrate promotion docs/comments now describe promotion as a materialization
  DAG step and read-snapshot update, not as a refresh side effect. The
  `refresh_id` field remains the persisted substrate snapshot key.
- The substrate read-snapshot helper is now `update_read_snapshot()` rather
  than `refresh_read_snapshot()`, matching its role as a materialized reader
  availability copy.
- Central materialization/source prose now describes promotion as ordinary
  materialization, unavailable insight products as unavailable/outdated, and
  source products as materialization inputs rather than refresh-run state.
- MCP read tools now call `best_materialized_refresh_id` /
  `latest_materialized_refresh_id` directly instead of importing them through
  refresh-named local aliases.
- `substrate_materialization_snapshot()` now accepts
  `latest_materialized_refresh_id` and reports that key in materialization
  high-water metadata; external substrate payloads still expose `refresh_id`
  where they refer to the persisted snapshot key.
- Machine-analysis readiness table coverage now reports
  `materialized_snapshot_count` and `latest_materialized_refresh_id`, and its
  evidence text refers to materialized snapshots rather than refresh ids.
- `ensure_materialized(...)` now uses its requested `window` for continuous
  local products: an otherwise-ready product is only a no-op when known
  materialized bounds cover the requested window; event/export sources still
  report overlap/non-overlap without treating absence as zero or routine local
  work.
- Source readiness now routes materialized contract checks through
  `ensure_materialized(..., window=(start, end))` instead of passively reading
  audit rows. Readiness can therefore perform cheap local repair for owned
  products while preserving the requested-window context.
- Coverage reports now also route non-metadata local products through
  `ensure_materialized(..., window=(start, end))` before computing source
  coverage, and re-audit only when a product actually changed.
- Keylog text-content analysis is now a separate explicit surface:
  `sources.keylog.text_snapshots(...)`,
  `analysis.keylog.analyze_keylog_text_content(...)`, and MCP
  `keylog_text_content`. Keybind usage and text-shape metadata remain separate
  products, and current captures that contain no snapshot text simply report
  zero content rows.
- Materialized evidence-graph builds no longer silently drop ActivityWatch
  focus evidence. `evidence_activitywatch.add_focus(...)` now keeps the same
  focus span/deep-work/circadian/loop/fragmentation/attention/project-day
  source path regardless of graph mode.
- ActivityWatch now has graph-facing derived products under
  `derived_root/activitywatch/graph/`: focus spans, project focus days,
  deep-work blocks, circadian profiles, loops, fragmentation, and attention.
  The evidence graph prefers these products when present and falls back to the
  canonical event product when absent so correctness never depends on silence.
- `ensure_materialized(..., window=...)` now forwards the requested window to
  materializers that accept `start`/`end`, while preserving zero-argument
  materializers unchanged. The ActivityWatch derived materializer uses that
  window to recompute and replace only affected logical-day rows, preserving
  existing rows outside the requested interval.
- Materialized ActivityWatch graph reads now call
  `ensure_materialized("activitywatch_derived", window=...)` at the graph
  boundary before reading focus/deep-work/circadian/fragmentation/attention
  products. The graph still falls back to canonical events if product
  materialization fails, so correctness does not depend on product availability.
- ActivityWatch derived manifests now record exact `covered_dates`, and generic
  materialization coverage uses those dates instead of pretending a sparse
  first/last span is contiguous. Requested-window checks for dated derived
  products therefore rerun the product materializer when an interior logical day
  is missing.
- `keylog_analysis.json` is now a real analysis DAG product rather than only a
  standalone CLI command / materialization-policy name. The machine-analysis
  DAG writes it over the same rolling window used for command/workflow
  products, so `--up-to keylog_analysis` is executable and repeated reads can
  reuse the artifact instead of raw-scanning broad keylog windows.
- MCP `keybind_usage` and `keylog_text_shape` now prefer
  `keylog_analysis.json` when the artifact can answer the requested window
  without lying: covered-window reuse for daily keybind usage, keybind
  summaries, and text-shape metrics; temporal keybind buckets still require an
  exact-window artifact. Other requests fall back to live analysis.
- `keylog_analysis.json` now also carries text-content metrics as aggregates
  (`text_content` totals, daily rows, and top terms) without storing raw
  snapshot bodies. MCP `keylog_text_content` reuses that section for
  exact-window reads and falls back to live analysis when a broader artifact
  cannot honestly answer the requested top-term window.
- `operator_daily` keylog filling now reuses covering `keylog_analysis.json`
  keybind rows before falling back to live `analyze_keylog(...)`, so
  cross-source daily matrices do not raw-scan keylog metadata a second time
  after the machine-analysis DAG has already materialized that product.
- Machine-analysis materialization planning can now treat date-bounded JSON
  artifacts as ready when their declared `start`/`end` coverage contains the
  requested window. This removes one timer-only cause of redundant runs for
  products such as `keylog_analysis.json`; missing or non-covering artifacts
  still run.
- Substrate-promotion planning now uses the same requested-window rule for
  `substrate_source_status` rows: stale status timestamps do not trigger
  promotion when every required source status is ready and its recorded
  `window_start`/`window_end` contains the requested window. Non-ready,
  missing-window, or non-covering rows still run.
- Machine-analysis substrate promotion now materializes the same rolling
  90-day default window used by the downstream machine-analysis products
  instead of falling through to the promoter's unrelated previous-month
  default. Explicit machine-analysis `--start/--end` bounds are preserved.
- Substrate promotion now records durable `substrate_run_step` rows around
  each promoter family: artifacts, AI work events, Polylogue timeline,
  evidence graph, PR review, work observations, personal sources, and machine
  tables. Long rolling-window promotes can now show which stage is running,
  which stage failed, and how many rows each completed stage added.
- Evidence-graph ActivityWatch nodes now avoid the prompt-facing
  `focus_timeline` path and skip raw ActivityWatch detail entirely for
  materialized graph builds. The canonical ActivityWatch event file is 561 MB,
  so detailed focus evidence needs a proper materialized daily/span product
  rather than an incidental raw scan during substrate promotion.
- Machine-analysis substrate promotion no longer requests the general
  evidence-graph source. Machine-analysis consumers use machine telemetry and
  work-observation substrate tables; current-state promotion remains the owner
  of broad evidence-graph materialization.
- Work-observation substrate promotion now streams xtask, Polylogue devtool,
  stage, and test-result source iterables into the substrate batch writer
  instead of first materializing all rows as Python lists. This keeps peak
  memory bounded for the high-volume test-result ledger.
- Current-state substrate promotion now declares its substrate source contract
  (`commits`, `file_changes`, `symbols`, AI work events, evidence graph,
  PR review, work observations) to the materialization planner, so dry-runs can
  skip or run from source-status coverage instead of leaving the step in
  inspect-only ambiguity.
- Materialized evidence-graph builds now also skip personal-product and
  temporal-signal add-ons. In measured 2026-06-01..2026-06-06 builds, those
  add-ons cost roughly 73s and 119s respectively for very few nodes; they remain
  available through explicit/detail graph paths until they have cheap
  product-backed readers.
- Materialized evidence-graph builds now also skip the machine-analysis artifact
  overlay. The overlay eagerly loads many machine JSON products and is valuable
  for explicit inspection, but it is not cheap enough for transparent
  current-state graph reads until the useful subset has a small product-backed
  reader.
- Evidence-graph finalization now asks source-readiness for audit-only caveats
  on materialized graph reads: no Polylogue heavy counts, no analysis inventory
  scan, and no inline coverage/materialization repair. Source-readiness and
  coverage audit commands retain repair behavior when called directly.
- Evidence-graph analysis overlays now reuse one generated-artifact inventory
  for artifact nodes and claim extraction instead of walking and parsing the
  analysis output tree twice per graph build.
- Audit-only source-readiness no longer calls `source_observations()`, avoiding
  a second materialization-audit pass when coverage metadata is already being
  read for the same report.
- Webhistory reads now use a source-owned daily/domain index over canonical
  `full_history.ndjson`, keyed by the history file and manifest signatures.
  Materialized evidence-graph builds therefore emit web-domain nodes through the
  web source API without routing through cross-source daily-signal aggregates or
  rescanning the full 420 MB history file on hot reads.
- Materialized evidence-graph finalization now skips the full source-readiness
  audit. Base-source failures already provide caveats, and the cheap readiness
  node remains available; the expensive comprehensive readiness report belongs
  to explicit readiness/current-state surfaces rather than every graph build.
- Generated analysis artifact discovery now writes and reuses
  `.analysis_artifact_inventory.json` under the analysis output directory. The
  manifest is validated by per-file size/mtime signatures, so unchanged graph
  reads no longer reparse every generated JSON/Markdown artifact.
- Polylogue work-event graph evidence now catches unavailable work-event
  products at the local source boundary. Known Polylogue degradation no longer
  escapes into the graph source wrapper as a traceback-producing blocked source.
- Clipboard source reads now persist parsed per-file entries behind
  file-signature caches. Materialized evidence-graph builds can emit clipboard
  evidence again through the clipboard source API; changed live/export files
  rebuild their own parse cache instead of every graph read silently dropping
  clipboard context.
- Materialized evidence-graph builds now include direct sleep and health nodes
  from the existing sleep-architecture and daily-health summary sources, but
  keep the sleep-productivity correlation bridge out of the transparent path.
  The direct sources cost under a second on measured windows and produce useful
  evidence where coverage exists; the productivity join still needs a compact
  product-backed reader before it belongs in default materialized graph reads.
- Materialized evidence-graph builds now keep terminal session nodes and reuse
  those already-loaded sessions for terminal pattern detection. Pattern
  detection still reads the canonical Atuin command product once to assign raw
  commands to session windows, but it no longer repeats shell-session grouping
  just because a graph read wants both session and pattern evidence.
- Polylogue graph evidence now reads populated `session_profiles` and
  `session_work_events` SQLite product tables directly before falling back to
  the typed facade. This avoids treating present products as unavailable when
  facade readiness drifts, restores AI evidence nodes, and removes the
  cold-start readiness penalty from the default graph.
- File/symbol overlap edges no longer build a temporary DuckDB schema for each
  graph read. The graph already has the small relevant AI/commit node set in
  memory, so overlap edges are computed directly with the same path filtering,
  +/-24h window, and symbol suffix-match semantics.
- Analysis claim extraction now has a claim manifest beside the artifact
  inventory. The first read after artifact changes extracts typed claims from
  JSON products; unchanged graph reads reuse the manifest instead of reparsing
  every claim-bearing artifact.
- Polylogue work-event graph attribution now trusts session-level project
  products before falling back to expensive path resolution. Direct
  `/realm/project/<name>` paths are still picked up cheaply, but generated or
  external paths no longer force thousands of `Path.relative_to` checks when
  the session already carries the project.
- MCP `machine_workflow_mechanics` now prefers the materialized
  `workflow_mechanics.json` artifact for the broad default read. Filtered
  project/window, explicit snapshot, custom retry gap, and custom limit calls
  still use live analysis until the artifact carries enough compatibility
  metadata to answer them honestly.
- Source contracts now declare local materializer executors for all products
  already present in the materializer registry, including export coalescers
  such as Spotify, Reddit, Messenger, Raindrop, communications, bookmarks, and
  ARBTT. These contracts mean "can rebuild/coalesce the local product from
  existing local inputs"; acquiring newer third-party exports remains outside
  Lynchpin's local materialization boundary.
- Export coalescer manifests now record input high-water metadata
  (`input_file_count`, `input_latest_mtime`), and `ensure_materialized(...)`
  exposes those fields through `source_high_water` so read paths can see the
  local input state a product was built from.
- Export coalescer audits now use manifest input high-water metadata: when a
  manifest says it was built from a different local input count or latest input
  mtime, the product is `partial` rather than `ready`, so ordinary
  `ensure_materialized(...)` calls can rebuild it inline.
- Browser bookmark and ARBTT materializers now emit the same input high-water
  metadata, and their audit rows use it to avoid treating products built from
  older local input files as ready/no-op.
- The communications materializer now does the same for composed local inputs:
  canonical Messenger events, Outlook CSV exports, and Teams viability logs.
  Input changes make the communication product `partial`, allowing ordinary
  `ensure_materialized(...)` paths to repair it instead of requiring an explicit
  refresh concept.
- IRC materialization now records raw WeeChat log input high-water as well, and
  audit treats a product built from older logs as `partial`. This removes
  another hidden "remember to refresh" edge from ordinary IRC reads.
- Atuin command-history materialization now records the source SQLite DB
  high-water and audit downgrades products built from an older DB to `partial`,
  so shell-history reads can converge from the current local database without a
  separate refresh command.
- ActivityWatch event materialization now records live/archive DB input
  high-water and audit rejects products built from older local DB inputs. This
  keeps the continuous focus substrate aligned with the current machine without
  scanning databases on every read.
- Activity-content daily materialization now records upstream product
  high-water for canonical ActivityWatch events and title metadata. If either
  upstream product changes, the daily content product becomes `partial` and can
  be repaired by the same transparent materialization path.
- Title-metadata materialization now emits standard input high-water fields for
  its source DuckDB, and audit uses both the existing `source_db_mtime` and the
  standard input fields to reject products built from an older classification DB.
- Spotify-daily materialization now records canonical Spotify stream-product
  high-water and audit rejects daily aggregates built from an older stream
  product, keeping the aggregate aligned without forcing explicit refresh calls.
- Personal daily-signal materialization now records high-water for the upstream
  materialized product files that actually contributed rows. Audit compares
  those declared input paths directly, avoiding recursive self-audit while still
  making the aggregate partial when an upstream product changes.
- Machine telemetry materialization now records source SQLite high-water and
  audit rejects NDJSON table sets built from an older telemetry DB. This keeps
  the continuous machine substrate aligned using one cheap file-stat comparison.
- Webhistory full-history materialization now records canonical segment file
  high-water, and audit rejects merged history products built from older segment
  files. Browser-history reads can therefore repair from local segments without
  depending on a remembered refresh step.
- Google Takeout inventory and typed-product materializers now record raw archive
  high-water, and audit downgrades typed products built from older archives to
  `partial`. Local export coalescing now follows the same transparent repair
  rule as Spotify/Reddit/Raindrop.
- Normal CLI/MCP `observability-status` surfaces now omit diagnostic queue state
  by default; queue details remain available only through explicitly diagnostic
  ledger/queue commands.
- Named analysis-artifact reads now annotate materialization status with the
  requested artifact path/name/status. If the generic artifact inventory is
  ready but the specific requested JSON object is missing or malformed, the
  returned status is target-specific (`missing`/`malformed`) instead of falsely
  reporting generic readiness.
- `machine-status` now ensures the analysis-artifact product once before
  reading its generated status inputs. The command remains a status read, but it
  no longer depends on a remembered prior artifact refresh.
- Context-pack machine-analysis rendering now also ensures the analysis-artifact
  product once before loading machine-analysis JSONs, then continues to render
  explicit missing/malformed artifact lines if the local materialization cannot
  produce them.
- Multi-artifact readers can now reuse one analysis-artifact materialization
  status across named loads. Current-state dashboard and machine-readiness reads
  use that path, avoiding repeated artifact-inventory audits while preserving
  target-specific missing/malformed reporting.
- MCP artifact inventory/read tools now materialize the analysis-artifact
  product before discovery, reuse that one status inside the response, and
  report unresolved selectors as target-specific missing/blocked states rather
  than treating artifact discovery as a separate remembered refresh step.
- The older list-shaped MCP `analysis_artifact_status` tool now also
  materializes the analysis-artifact product before listing, so all
  agent-facing artifact discovery surfaces share the same transparent
  convergence rule.
- Normal MCP runtime/substrate readiness status now exposes
  `latest_materialized_refresh_id` alongside the schema-compatible
  `latest_refresh_id`, and prose/comments describe materialized snapshots rather
  than treating refresh IDs as the operator model.
- MCP velocity/throughput read tools now describe defaults as materialized
  substrate snapshots and expose `materialized_refresh_id` alongside the stored
  `refresh_id` key in structured outputs where a snapshot is selected.
- MCP velocity/throughput reads now cheaply observe/converge the derived
  `evidence_graph_substrate` product before selecting snapshot rows. Dict-shaped
  responses include the materialization status, while list-shaped rows retain
  their bounded row shape and still perform the pre-read convergence.
- `load_evidence_graph_summary` now performs the same pre-read substrate
  materialization observation and returns that status on both matching and
  missing-build responses.
- `contract_coverage(source=...)` now calls `ensure_materialized(...)` with the
  requested window before reporting the selected dataset. If inline
  materialization changes that product, the row is re-audited before coverage is
  computed. Unfiltered `contract_coverage()` remains a cheap audit so
  broad `analysis_readiness()` does not accidentally materialize every local
  product.
- Personal-source MCP read tools now ensure their owned canonical source product
  before iterating it: webhistory, Google Takeout products, Atuin terminal,
  bookmarks, communications, ActivityWatch/ARBTT focus, title metadata, and
  activity-content/title joins. Windowed reads pass the requested window into
  `ensure_materialized(...)`; unwindowed searches only perform product-level
  convergence.
- Materialized graph builds now include the canonical `personal_daily_signals`
  product directly. The product materializer accepts half-open windows, merges
  only those rows into the existing NDJSON, records exact `covered_dates`
  including zero-row days, and the graph calls `ensure_materialized(...)` before
  reading instead of skipping the legacy broad personal-product builder.
- Temporal graph signals now follow the same pattern: deterministic anomaly,
  trend, changepoint, and rhythm events are written to a canonical temporal
  signal product, window updates merge into existing rows with exact
  `covered_dates`, and materialized graph mode ensures/reads that product
  instead of omitting temporal signals.
- Sleep-productivity graph links are now a canonical derived product rather than
  an inline graph-only bridge. The product uses half-open window updates with
  exact `covered_dates`; materialized health graph nodes ensure/read it and emit
  a distinct `sleep_productivity_link` kind instead of overloading
  `sleep_quality`.
- Source-contract defaulting now classifies derived/stage products as
  `materialization_mode="derived"` even when they have a local materializer.
  Event/capture products with materializers remain `local`; the contract no
  longer lies about derived products merely because Lynchpin can rebuild them
  inline.
- Change/churn MCP tools that read promoted DuckDB tables now perform the same
  cheap `evidence_graph_substrate` materialization observation before selecting a
  snapshot: refactor candidates, file hotspots, conventional commits, breaking
  changes, commit-kind attribution, and symbol churn hotspots.
- View-backed MCP tools now also observe/converge the derived substrate before
  reading project-day correlations, closure chains, overlap edges, review SLOs,
  project-pair signals, and evidence walks. Dict-shaped view responses surface
  that materialization status where it helps explain missing snapshots.
- Substrate-backed signal MCP tools now use the same pre-read materialization
  observation before source co-occurrence, AI/commit lag, project health, daily
  rhythm fingerprints, and operator-day correlations. Dict-shaped signal
  responses carry materialization metadata for no-data and interpreted result
  cases.
- Review MCP tools now observe/converge the derived substrate before reading
  review rows or bottleneck summaries. `pr_review_rows()` also defaults to the
  best materialized `pr_review_row` snapshot instead of returning duplicate rows
  across every historical snapshot.
- Health/audit MCP reads now follow the same transparent default-read rule:
  gap drafts, confidence matrices, kind audits, work-package durability,
  evidence confidence, source anomalies, health trends, and cleanup-period
  detection materialize/observe the derived substrate before selecting default
  snapshots. Explicit historical `refresh_id` reads remain deterministic and do
  not trigger a new materialization pass.
- The remaining personal MCP substrate reads now converge their owned source
  products and the promoted substrate before default snapshot selection:
  `spotify_daily`, `personal_daily_signals`, and `operator_rhythm`. The rhythm
  tool also ensures ActivityWatch over the requested window before composing
  focus rows with promoted commit/AI/machine timestamps.
- Substrate/admin claim reads now converge the derived substrate before default
  `analysis_claims` snapshot selection. Broad source-status reads remain cheap
  inventory/status views, and update/backfill paths remain explicit operations
  rather than hidden transparent-read side effects.
- Machine MCP promoted-table reads now converge canonical machine telemetry and
  the derived substrate before default snapshot selection for daily metrics,
  context-segmented metrics, and service-state summaries. Generation-history
  reads converge the derived substrate before reading the promoted
  `sinnix_generation` stage.
- `machine_bufferbloat_summary` now follows the same snapshot discipline: it
  converges machine telemetry/substrate on default reads, selects the best
  materialized `machine_network_sample` snapshot, and the substrate reader
  filters by `refresh_id` so historical materialization snapshots are not
  double-counted in daily bufferbloat aggregates.

## Remaining Product Gaps

- Local continuous source materializers are now window-aware by contract:
  ActivityWatch, Atuin, IRC, machine telemetry, and webhistory all accept
  half-open `start`/`end` windows. Requested-window repairs replace only the
  affected logical days where the product format supports it, and manifests
  expose `covered_dates` so sparse products are not mistaken for contiguous
  coverage.
- The next simplification step is source-delta precision, not more refresh
  machinery: ActivityWatch canonical event repair now records precise
  `covered_dates`, including zero-row repaired days, so sparse products are not
  mistaken for contiguous coverage, and it queries window/AFK/web buckets in a
  single raw DB pass for each repair. Raw DB candidate bounds are cached by file
  signature so windowed repairs skip archive DBs whose ActivityWatch event span
  cannot overlap the requested window. It should eventually append or replace
  by source DB high-water metadata when a narrower delta is known. Webhistory
  segment selection already uses filename coverage when possible; unknown
  segment bounds still intentionally fall back to parsing those files for
  correctness.
- Whole-export raw iterators still perform product-level ensures by default,
  which is acceptable for unwindowed export entry points. Spotify, Reddit,
  Messenger, Raindrop, Google Takeout, and Gmail date-windowed aggregate readers
  now perform a single windowed ensure before reading and suppress the
  iterator-level product ensure when a caller already converged the product.
  Webhistory daily/domain readers and activity-content readers follow the same
  pattern: direct calls still converge by default, while graph/MCP/operator
  paths that already converged the product read with `ensure=False`. Operator
  and MCP daily reads also suppress duplicate Atuin/Spotify/personal-signal
  ensures after their explicit windowed pre-read convergence.
- IRC source reads now converge the canonical IRC event product by default.
  Public daily reads convert their inclusive date API to the materializer's
  half-open window, while graph/operator paths that already converged the
  product pass `ensure=False`.
- GitHub context materialization uses a 48h network-refresh contract. Products
  older than that trigger a network refresh with cache bypass; if the network
  refresh fails, the result is `blocked` rather than `ready` so stale GitHub
  lifecycle data cannot silently satisfy freshness-sensitive reads.
- Operator keybind usage now converges `keylog_analysis` over the requested
  half-open window before reading `keylog_analysis.json` or falling back to live
  analysis, so high-level keybind frequency rows are product-first rather than
  opportunistic artifact reads.
- Personal daily-signal materialization audits upstream product readiness once
  and suppresses nested ensures for source readers that are already proven
  overlapping/ready, including webhistory, activity-content, bookmarks,
  communications, ARBTT, Spotify, Reddit, Messenger, and Raindrop. Derived-product
  refresh is therefore closer to one product audit plus one bounded read per
  upstream.
- Personal-product evidence graph reads apply the same rule for explicitly
  pre-converged bookmark, communication, and ARBTT products, avoiding immediate
  re-audit after `_ensure_source(...)`.
- IRC conversation extraction now accepts the same `ensure=False` handoff, so
  standalone IRC evidence graph construction can pre-converge `irc` once and
  then read conversation clusters without re-entering materialization.
- Temporal signal materialization now converges upstream products over the
  anomaly baseline-extended window once, then runs default detectors with
  nested input ensures disabled. ActivityWatch temporal signals read the
  graph-facing `activitywatch_derived` product directly instead of rebuilding
  raw composite daily activity during temporal detection.
- Substrate snapshot promotion now applies the same preconverged-read rule for
  personal daily signals and activity-content products: after the snapshot
  ensures those products, promotion reads them with `ensure=False`.
- MCP personal-source tools now also honor preconverged reads for bookmark,
  communication, and ARBTT products instead of re-ensuring immediately after
  their explicit source materialization check.
- Active substrate personal-source promotion now reads Spotify daily,
  activity-content, and personal daily-signal products with `ensure=False`
  after its `ensure_input_product(...)` gate succeeds.
- The canonical web visit iterator now accepts an `ensure` handoff and passes a
  half-open materialization window when it does self-converge. MCP `web_daily`
  preconverges webhistory once, then reads visits with `ensure=False`.

## Test Plan

- Unit tests for `ensure_materialized(...)`:
  - local missing product runs materializer inline;
  - unchanged product no-ops from manifest/high-water;
  - continuous ready products with missing requested window coverage run the
    local materializer instead of returning a misleading no-op;
  - event/export products outside the requested window report non-overlap
    without being rebuilt or interpreted as zero activity;
  - source-readiness checks forward their requested window into
    `ensure_materialized(...)`;
  - coverage reports forward their requested window into
    `ensure_materialized(...)` for locally materializable products and re-audit
    only after an inline update;
  - every registered local materializer is backed by a source contract whose
    materialization mode is `local`;
  - local product manifests expose input high-water metadata through
    `ensure_materialized(...)`;
  - local products with changed manifest input high-water are not treated as
    ready/no-op;
  - coverage-bound source returns `coverage_bound` and creates no control-plane work;
  - manual source returns `manual` and creates no control-plane work;
  - live source returns `ready` or source-owned degraded status and writes no
    control-plane work;
  - budget mismatch returns `blocked` instead of queueing.
- Integration tests for MCP artifact reads:
  - owned artifact triggers materialization when missing;
  - second read is cheap/no-op;
  - no queue/control-plane work is created.
- DAG tests:
  - unchanged machine DAG/product family skips from manifests;
  - changed upstream high-water triggers dependent product rebuild;
  - failed materializer records product failure diagnostics.
- Sinnix tests:
  - daily `lynchpin-materialize` timer/service still evaluates;
  - frequent `lynchpin-refresh-worker` timer is absent or disabled by default;
  - background/heavy service uses the expected slice/weights.
- Keylog tests:
  - current inference matches known sample binds;
  - future modifier-state samples produce exact bind counts;
  - text-content product can be generated independently of keybind aggregates.
- Regression checks:
  - existing `materialization_status`, `mcp_capability_matrix`, artifact reads,
    current-state/context-pack, operator_daily, and machine status still work.

## Assumptions

- Breaking cleanup is acceptable when it removes obsolete refresh machinery.
- The desired user model is "products are just current" rather than "refresh is
  visible and managed."
- Normal workload is small enough that cheap local work should run inline.
- Heavy work can remain daily/background/manual, but not through a constantly
  emphasized queue.
- No VPS solution is part of the current implementation path.
- No privacy constraint is being imposed on keylog/text analysis by this plan.
  Product boundaries exist for clarity and control, not prohibition.
- Devloop extraction should be narrow and evidence-backed; it should not become
  another custom verification ontology.
