import json
import re
from pathlib import Path
from datetime import date

from lynchpin.analysis.core.dag import StepStatus
from lynchpin.analysis.materialize import (
    _rolling_window,
    current_state_dag,
    machine_analysis_dag,
)


def test_analysis_spec_artifact_catalog_covers_materialization_json_outputs():
    spec = json.loads(Path("lynchpin/analysis/analysis_spec.json").read_text(encoding="utf-8"))
    materialize_source = Path("lynchpin/analysis/materialize.py").read_text(encoding="utf-8")
    outputs = {
        left or right
        for left, right in re.findall(
            r'_out\("([^"]+\.json)"\)|_out\(\'([^\']+\.json)\'\)',
            materialize_source,
        )
    }
    # Best-effort input read by substrate promotion; no DAG step writes it.
    outputs.discard("active_pr_review_topology.json")

    assert outputs <= set(spec["artifacts"].values())


def test_machine_command_window_defaults_to_rolling_90_days():
    start, end = _rolling_window(start=None, end=date(2026, 6, 1), days=90)

    assert start == date(2026, 3, 4)
    assert end == date(2026, 6, 1)


def test_machine_command_window_preserves_explicit_bounds():
    start, end = _rolling_window(start=date(2026, 5, 1), end=date(2026, 5, 5), days=90)

    assert start == date(2026, 5, 1)
    assert end == date(2026, 5, 5)


def test_machine_analysis_dag_promotes_rolling_window_when_start_is_omitted(monkeypatch):
    calls = []

    def fake_promote(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr("lynchpin.analysis.materialize.run_substrate_promote", fake_promote)

    results = machine_analysis_dag(start=None, end=date(2026, 6, 1)).run(
        up_to="machine_analysis_substrate_promote"
    )

    assert results[0].status == StepStatus.SUCCESS
    assert calls[0]["window_start"] == date(2026, 3, 4)
    assert calls[0]["window_end"] == date(2026, 6, 1)
    assert calls[0]["refresh_id"] == "machine-analysis:rolling:2026-06-01"


def test_machine_analysis_dag_promotes_explicit_window(monkeypatch):
    calls = []

    def fake_promote(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr("lynchpin.analysis.materialize.run_substrate_promote", fake_promote)

    results = machine_analysis_dag(start=date(2026, 5, 1), end=date(2026, 5, 5)).run(
        up_to="machine_analysis_substrate_promote"
    )

    assert results[0].status == StepStatus.SUCCESS
    assert calls[0]["window_start"] == date(2026, 5, 1)
    assert calls[0]["window_end"] == date(2026, 5, 5)
    assert calls[0]["refresh_id"] == "machine-analysis:2026-05-01:2026-05-05"


def test_current_state_refresh_dag_is_independent_of_external_repos():
    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_EVIDENCE_GRAPH,
        SOURCE_MACHINE,
        SOURCE_MACHINE_CGROUP_MEMORY,
        SOURCE_MACHINE_PROCESS_MEMORY,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_SPOTIFY_DAILY,
        SOURCE_WORK_OBSERVATIONS,
    )
    from lynchpin.analysis.materialize import CURRENT_STATE_SUBSTRATE_SOURCES
    from lynchpin.analysis.materialize import MACHINE_ANALYSIS_SUBSTRATE_SOURCES

    assert SOURCE_SPOTIFY_DAILY not in CURRENT_STATE_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE not in CURRENT_STATE_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE_SERVICE_STATE not in CURRENT_STATE_SUBSTRATE_SOURCES
    assert SOURCE_EVIDENCE_GRAPH in CURRENT_STATE_SUBSTRATE_SOURCES
    assert SOURCE_WORK_OBSERVATIONS in CURRENT_STATE_SUBSTRATE_SOURCES
    assert SOURCE_EVIDENCE_GRAPH not in MACHINE_ANALYSIS_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE in MACHINE_ANALYSIS_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE_PROCESS_MEMORY in MACHINE_ANALYSIS_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE_CGROUP_MEMORY in MACHINE_ANALYSIS_SUBSTRATE_SOURCES
    assert SOURCE_MACHINE_SERVICE_STATE in MACHINE_ANALYSIS_SUBSTRATE_SOURCES
    assert SOURCE_WORK_OBSERVATIONS in MACHINE_ANALYSIS_SUBSTRATE_SOURCES

    results = current_state_dag(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
    ).run(dry_run=True)

    names = [result.name for result in results]
    expected = {
        "active_project_snapshot",
        "active_code_inventory",
        "active_python_complexity",
        "active_python_import_graph",
        "active_git_facts",
        "code_history_claims",
        "active_rust_workspace_graph",
        "active_work_packages",
        "project_velocity_windows",
        "active_code_hotspots",
        "active_quality_guardrails",
        "active_structural_findings",
        "active_semantic_static_findings",
        "active_rust_dependency_hygiene",
        "active_python_dependency_hygiene",
        "active_symbol_index",
        "active_symbol_changes",
        "active_ci_health",
        "active_commit_semantics",
        "active_ai_attribution",
        "current_state_substrate_promote",
        "machine_analysis_substrate_promote",
        "machine_telemetry_analysis",
        "machine_episode_analysis",
        "machine_pressure_incidents",
        "machine_below_analysis",
        "machine_below_attribution",
        "machine_below_export_handoff",
        "machine_context_windows",
        "machine_work_observations",
        "workflow_mechanics",
        "machine_analysis_feature_frames",
        "machine_mining",
        "machine_dataset_diagnostics",
        "machine_validation_design",
        "machine_matched_designs",
        "machine_negative_controls",
        "machine_comparisons",
        "machine_work_state_windows",
        "command_performance_windows",
        "machine_observational_deltas",
        "machine_attribution_candidates",
        "machine_derivation_inventory",
        "machine_benchmark_plans",
        "machine_benchmark_manifest_bundle",
        "machine_benchmark_preflight",
        "machine_benchmark_execution_handoff",
        "machine_experiment_manifest_diagnostics",
        "machine_support_assessment",
        "machine_mechanism_hypotheses",
        "machine_instrumentation_gaps",
        "machine_calibration_fixtures",
        "machine_measurement_system",
        "machine_attribution_claims",
        "machine_assumption_checks",
        "devshell_performance",
        "machine_observational_baselines",
        "machine_experiment_claims",
        "machine_analysis_readiness",
        "machine_gap_summary",
        "keylog_analysis",
        "current_state_operator_day",
        "current_state_context",
        "current_state_narrative",
    }
    assert set(names) == expected
    assert names.index("machine_episode_analysis") < names.index("machine_context_windows")
    assert names.index("machine_episode_analysis") < names.index("machine_below_attribution")
    assert names.index("machine_below_analysis") < names.index("machine_below_attribution")
    assert names.index("machine_work_observations") < names.index("machine_below_attribution")
    assert names.index("machine_below_attribution") < names.index("machine_below_export_handoff")
    assert names.index("machine_context_windows") < names.index("machine_observational_baselines")
    assert names.index("machine_context_windows") < names.index("machine_work_state_windows")
    assert names.index("machine_work_state_windows") < names.index("command_performance_windows")
    assert names.index("command_performance_windows") < names.index("machine_observational_deltas")
    assert names.index("machine_observational_deltas") < names.index("machine_attribution_candidates")
    assert names.index("machine_work_observations") < names.index("machine_attribution_candidates")
    assert names.index("machine_work_observations") < names.index("workflow_mechanics")
    assert names.index("machine_analysis_feature_frames") < names.index("machine_mining")
    assert names.index("machine_mining") < names.index("machine_dataset_diagnostics")
    assert names.index("machine_analysis_feature_frames") < names.index("machine_validation_design")
    assert names.index("machine_dataset_diagnostics") < names.index("machine_comparisons")
    assert names.index("machine_mining") < names.index("machine_comparisons")
    assert names.index("machine_validation_design") < names.index("machine_comparisons")
    assert names.index("machine_validation_design") < names.index("machine_matched_designs")
    assert names.index("machine_matched_designs") < names.index("machine_negative_controls")
    assert names.index("machine_negative_controls") < names.index("machine_comparisons")
    assert names.index("machine_matched_designs") < names.index("machine_comparisons")
    assert names.index("machine_comparisons") < names.index("machine_attribution_candidates")
    assert names.index("machine_matched_designs") < names.index("machine_attribution_candidates")
    assert names.index("machine_attribution_candidates") < names.index("machine_benchmark_plans")
    assert names.index("machine_derivation_inventory") < names.index("machine_benchmark_plans")
    assert names.index("machine_benchmark_plans") < names.index("machine_benchmark_manifest_bundle")
    assert names.index("machine_benchmark_manifest_bundle") < names.index("machine_benchmark_preflight")
    assert names.index("machine_benchmark_preflight") < names.index("machine_support_assessment")
    assert names.index("machine_benchmark_manifest_bundle") < names.index("machine_support_assessment")
    assert names.index("machine_benchmark_plans") < names.index("machine_support_assessment")
    assert names.index("machine_experiment_claims") < names.index("machine_support_assessment")
    assert names.index("machine_experiment_manifest_diagnostics") < names.index("machine_analysis_readiness")
    assert names.index("machine_support_assessment") < names.index("machine_mechanism_hypotheses")
    assert names.index("machine_support_assessment") < names.index("machine_instrumentation_gaps")
    assert names.index("machine_support_assessment") < names.index("machine_benchmark_execution_handoff")
    assert names.index("machine_support_assessment") < names.index("machine_attribution_claims")
    assert names.index("machine_experiment_claims") < names.index("machine_attribution_claims")
    assert names.index("machine_attribution_claims") < names.index("machine_assumption_checks")
    assert names.index("machine_analysis_feature_frames") < names.index("machine_measurement_system")
    assert names.index("machine_experiment_claims") < names.index("machine_measurement_system")
    assert names.index("machine_work_observations") < names.index("machine_measurement_system")
    assert names.index("command_performance_windows") < names.index("devshell_performance")
    assert names.index("machine_gap_summary") < names.index("machine_analysis_readiness")
    assert names.index("machine_analysis_substrate_promote") < names.index("machine_telemetry_analysis")
    assert names.index("machine_analysis_substrate_promote") < names.index("machine_gap_summary")
    assert names.index("code_history_claims") < names.index("current_state_substrate_promote")
    assert names.index("current_state_substrate_promote") < names.index("current_state_context")
    assert names.index("machine_work_state_windows") < names.index("machine_observational_baselines")
    assert names.index("machine_experiment_claims") < names.index("machine_analysis_readiness")
    assert names.index("machine_dataset_diagnostics") < names.index("machine_analysis_readiness")
    assert names.index("machine_comparisons") < names.index("machine_analysis_readiness")
    assert names.index("machine_validation_design") < names.index("machine_analysis_readiness")
    assert names.index("machine_matched_designs") < names.index("machine_analysis_readiness")
    assert names.index("machine_negative_controls") < names.index("machine_analysis_readiness")
    assert names.index("machine_derivation_inventory") < names.index("machine_analysis_readiness")
    assert names.index("machine_benchmark_plans") < names.index("machine_analysis_readiness")
    assert names.index("machine_benchmark_manifest_bundle") < names.index("machine_analysis_readiness")
    assert names.index("machine_benchmark_preflight") < names.index("machine_analysis_readiness")
    assert names.index("machine_benchmark_execution_handoff") < names.index("machine_analysis_readiness")
    assert names.index("machine_experiment_manifest_diagnostics") < names.index("machine_analysis_readiness")
    assert names.index("machine_support_assessment") < names.index("machine_analysis_readiness")
    assert names.index("machine_mechanism_hypotheses") < names.index("machine_analysis_readiness")
    assert names.index("machine_instrumentation_gaps") < names.index("machine_analysis_readiness")
    assert names.index("machine_calibration_fixtures") < names.index("machine_analysis_readiness")
    assert names.index("machine_measurement_system") < names.index("machine_analysis_readiness")
    assert names.index("machine_attribution_claims") < names.index("machine_analysis_readiness")
    assert names.index("machine_assumption_checks") < names.index("machine_analysis_readiness")
    assert names.index("machine_analysis_readiness") < names.index("current_state_context")
    assert names.index("machine_experiment_claims") < names.index("current_state_context")
    assert names[-1] == "current_state_narrative"
    assert results[0].status == StepStatus.PENDING


def test_machine_analysis_dag_is_source_selected():
    names = [
        result.name for result in machine_analysis_dag(start=date(2026, 5, 1), end=date(2026, 5, 5)).run(dry_run=True)
    ]

    assert names[0] == "machine_analysis_substrate_promote"
    assert "substrate_promote" not in names
    assert "current_state_substrate_promote" not in names
    assert "active_git_facts" not in names
    assert "polylogue_metrics" not in names
    assert "machine_analysis_readiness" in names
    assert names.index("machine_analysis_substrate_promote") < names.index("machine_telemetry_analysis")
    assert names.index("machine_calibration_fixtures") < names.index("machine_analysis_readiness")
    assert names.index("machine_measurement_system") < names.index("machine_analysis_readiness")
    assert names.index("machine_dataset_diagnostics") < names.index("machine_analysis_readiness")
