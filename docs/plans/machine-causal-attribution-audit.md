---
status: active
purpose: acceptance audit for machine causal attribution infrastructure
updated: 2026-06-01
---

# Machine Causal Attribution Infra Audit

This is the non-execution acceptance record. "Infra-complete" here excludes
the empirical campaign itself: no controlled benchmark run is required, and no
controlled claim should be expected until run logs and telemetry are ingested.

## Current Verdict

Infra is complete for the current scope. The remaining blockers reported by
`machine-status` are empirical by design:

- controlled benchmark plans and execution queues exist, but no controlled
  benchmark has been run;
- insufficient-support rows remain explicit refusals until controlled runs or
  stronger natural-experiment evidence exist.

That is not missing infra. It is the support ladder refusing to invent evidence.

## Live State

Latest full `refresh-machine` completed with all expected machine artifacts
present:

- expected status artifacts: 11;
- available status artifacts: 11;
- benchmark execution queue: 10 groups, 120 run templates, 120 ready runs;
- benchmark preflight: 120/120 ready runs, 0 issues, warnings only;
- claim ledger: 25 claims, split into 12 natural-experiment and 13
  insufficient-support/refusal rows;
- controlled claim count: 0, because execution is explicitly out of scope.

Generated artifacts are under `.lynchpin/generated/analysis/`, especially:

- `machine_work_observations.json`;
- `machine_analysis_feature_frames.json`;
- `machine_mining.json`;
- `machine_validation_design.json`;
- `machine_matched_designs.json`;
- `machine_negative_controls.json`;
- `machine_attribution_candidates.json`;
- `machine_benchmark_plans.json`;
- `machine_benchmark_manifest_bundle.json`;
- `machine_benchmark_preflight.json`;
- `machine_benchmark_execution_queue.json`;
- `machine_support_assessment.json`;
- `machine_instrumentation_gaps.json`;
- `machine_attribution_claims.json`;
- `machine_analysis_readiness.json`;
- `machine_refresh_report.json`.

## Acceptance Matrix

| Requirement | Status | Evidence |
|---|---:|---|
| Work observations have parent/child analytics and MCP readers | Done | `lynchpin/analysis/machine/work_observations.py`; MCP tools `machine_work_observation_daily`, `machine_work_stage_summary`, `machine_work_test_summary`, `machine_work_slow_tests`, `machine_work_stage_daily`, `machine_work_failures`; `tests/analysis/test_analysis_machine_work_observations.py`; `tests/mcp/test_mcp_machine.py` |
| Extant dataset mining emits cohorts, boundaries, matched comparisons, scan registries, splits, negative controls, anomaly clusters, lagged exposures, and instrumentation gaps | Done | `feature_frames.py`, `mining.py`, `validation_design.py`, `matched_designs.py`, `negative_controls.py`, `instrumentation_gaps.py`; tests named for each module |
| Feature frames enforce exposure/outcome separation, leakage rejection, missingness flags, and censored rows | Done | `lynchpin/analysis/machine/feature_frames.py`; `tests/analysis/test_analysis_machine_feature_frames.py` |
| Controlled benchmark plans exist and validate manifests | Done | `benchmark_plans.py`, `controlled_benchmarks.py`, `experiment_manifest_diagnostics.py`, `benchmark_preflight.py`, `benchmark_manifest_bundle.py`; corresponding tests |
| Plans persist pre-analysis records, causal models, DOE ceilings, instrumentation bundles, and mandatory plan/run linkage | Done | `benchmark_plans.py`, `causal_model.py`, `benchmark_manifest_bundle.py`, `support_assessment.py`; `tests/analysis/test_analysis_machine_benchmark_plans.py`; `tests/analysis/test_analysis_machine_causal_model.py` |
| Nix internal-json logs have parser, row model, fixtures, and summaries | Done | `nix_internal_json.py`; integration in `controlled_benchmarks.py` and `experiments.py`; `tests/analysis/test_analysis_machine_nix_internal_json.py` |
| Attribution candidates cover cohorts, work, test, failure, boundary, pressure, cache, anomaly, lagged-exposure, and instrumentation-gap cases | Done | `attribution_candidates.py`; `tests/analysis/test_analysis_machine_attribution_candidates.py`; candidate detail MCP joins gaps/support/preflight |
| Controlled/natural/observational/insufficient claim promotion exists | Done | `experiments.py`, `support_assessment.py`, `attribution_claims.py`; tests for controlled, natural-experiment, observational, and refusal paths |
| Broad mined candidates carry scan denominators and multiplicity policy | Done | `mining.py`, `comparisons.py`, `dataset_diagnostics.py`; `tests/analysis/test_analysis_machine_mining.py`; `tests/analysis/test_analysis_machine_comparisons.py` |
| Exploratory mined patterns do not promote without validation, controls, or support ceilings | Done | `support_assessment.py`, `assumption_checks.py`, `negative_controls.py`; refusal tests in `test_analysis_machine_support_assessment.py` |
| Non-controlled claims include assumption ledgers and support ceilings | Done | `assumption_checks.py`, `support_assessment.py`, `attribution_claims.py`; MCP `machine_assumption_checks`, `machine_claim_evidence` |
| Graph includes candidate, plan, run, phase, estimate, claim, and refusal nodes with evidence edges | Done | `lynchpin/graph/machine_analysis.py`; `lynchpin/core/evidence_graph.py`; `tests/graph/test_evidence_graph_analysis.py` |
| MCP exposes inventory, mining scans, cohorts, frames, boundaries, comparisons, splits, controls, mechanisms, assumptions, gaps, candidates, readiness, plans, runs, phases, estimates, claims, slow tests/stages, failures, refresh health | Done | `lynchpin/mcp/tools/machine.py`; `tests/mcp/test_mcp_machine.py` |
| Current-state/status/dossier surfaces show the full ladder | Done | `status.py`; CLI `machine-status`; context pack machine section in `graph/context_pack.py`; `tests/graph/test_movement_context_pack.py` |
| Self-prompt artifacts exist for major implementation phases and rejected method-theater ideas are recorded | Done | `machine-causal-attribution-self-prompt.md`, especially implementation phase prompts and rejections |
| Refresh can build candidates and claim inputs without Polylogue repair | Done | `analysis/refresh.py` uses machine-specific substrate sources and disables evidence graph promotion for machine refresh |
| Source-selected refresh avoids unrelated personal-source promotion | Done | `MACHINE_ANALYSIS_SUBSTRATE_SOURCES`; `tests/analysis/test_analysis_machine_status_cli.py` covers the DAG source selection |
| Replay/calibration fixtures prove null, known-effect, broken-design, placebo, and missingness behavior | Done | `calibration.py`, `measurement_system.py`, support/negative-control tests |
| Focused tests cover every row in the test matrix | Done | One or more focused tests exist for each machine module, MCP surface, graph surface, and context/status surface |

## Why The Remaining Blockers Are Correct

`machine_status_payload()` intentionally treats these as blockers:

- no controlled benchmark claim despite manifest templates;
- insufficient-support assessments still present.

For the current scope, those blockers are not to be "fixed" by code. They are
the guardrail that prevents dry-run plans, observational scans, or incomplete
instrumentation from being narrated as controlled support. They should clear
only after future execution infra is used to run a campaign and ingest the
resulting manifests, logs, internal-json, and telemetry windows.

## Future Empirical Work

The next substantial non-infra workload is empirical:

1. choose a ready execution-queue group;
2. run the fixed derivation set under the generated warm/cold/randomized
   manifest schedule;
3. ingest run manifests, Nix internal-json logs, and telemetry windows;
4. recompute estimates/support assessments;
5. emit a controlled support-level claim or an explicit insufficient-support
   refusal.

Do not count that as unfinished infra unless a future run reveals a missing
parser, validator, linkage, or reader.
