import json
from types import SimpleNamespace

from lynchpin.analysis.core.status import MACHINE_ANALYSIS_ARTIFACTS, build_analysis_status


def test_analysis_status_uses_artifact_inventory(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    analysis_root = repo_root / ".lynchpin/generated/analysis"
    analysis_root.mkdir(parents=True)
    for name in (
        "sinex_structure_metrics.json",
        "sinex_temporal_metrics.json",
        "polylogue_metrics.json",
        "ecosystem_comparison.json",
        "work_package_scope.json",
        "ecosystem_dashboard.json",
        "ecosystem_dashboard.html",
        "commit_facts.json",
        "commit_shards.json",
        "active_project_snapshot.json",
        "active_commit_facts.json",
        "active_file_change_facts.json",
        "active_work_packages.json",
        "project_velocity_windows.json",
        "active_code_inventory.json",
        "active_python_complexity.json",
        "active_python_import_graph.json",
        "active_rust_workspace_graph.json",
        "active_code_hotspots.json",
        "active_quality_guardrails.json",
        "active_structural_findings.json",
        "active_semantic_static_findings.json",
        "active_rust_dependency_hygiene.json",
        "active_python_dependency_hygiene.json",
        "active_symbol_index.json",
        "active_symbol_changes.json",
        "active_symbol_diffs.json",
        "active_ci_health.json",
        "machine_telemetry_analysis.json",
        "machine_episode_analysis.json",
        "machine_below_analysis.json",
        "machine_below_attribution.json",
        "machine_context_windows.json",
        "machine_work_state_windows.json",
        "machine_work_observations.json",
        "machine_analysis_feature_frames.json",
        "machine_mining.json",
        "machine_dataset_diagnostics.json",
        "machine_validation_design.json",
        "machine_matched_designs.json",
        "machine_comparisons.json",
        "command_performance_windows.json",
        "machine_observational_deltas.json",
        "machine_attribution_candidates.json",
        "machine_derivation_inventory.json",
        "machine_benchmark_plans.json",
        "machine_benchmark_manifest_bundle.json",
        "machine_benchmark_preflight.json",
        "machine_benchmark_execution_handoff.json",
        "machine_below_export_handoff.json",
        "machine_experiment_manifest_diagnostics.json",
        "machine_support_assessment.json",
        "machine_attribution_claims.json",
        "machine_assumption_checks.json",
        "machine_mechanism_hypotheses.json",
        "machine_instrumentation_gaps.json",
        "machine_calibration_fixtures.json",
        "machine_measurement_system.json",
        "machine_negative_controls.json",
        "devshell_performance.json",
        "machine_observational_baselines.json",
        "machine_experiment_claims.json",
        "machine_gap_summary.json",
        "machine_analysis_readiness.json",
        "machine_analysis_materialization_report.json",
    ):
        (analysis_root / name).write_text('{"generated_at_utc":"2026-05-06T12:00:00+00:00"}', encoding="utf-8")
    (analysis_root / "machine_analysis_readiness.json").write_text(
        json.dumps({"dimensions": [{"dimension": "causal_support_gate", "status": "stable"}]}),
        encoding="utf-8",
    )

    spec_path = tmp_path / "analysis_spec.json"
    spec_path.write_text(json.dumps({}), encoding="utf-8")

    config = SimpleNamespace(
        analysis_output_dir=analysis_root,
        repo_root=repo_root,
        knowledgebase_root=tmp_path / "knowledgebase",
    )
    monkeypatch.setattr("lynchpin.analysis.core.status.get_config", lambda: config)
    monkeypatch.chdir(tmp_path)

    payload = build_analysis_status(spec_path=spec_path)

    assert payload["artifact_inventory"]["available_count"] == 65
    assert payload["families"]["active_project_snapshot"]["status"] == "stable"
    assert payload["families"]["active_git_facts"]["status"] == "stable"
    assert payload["families"]["active_work_packages"]["status"] == "stable"
    assert payload["families"]["project_velocity_windows"]["status"] == "stable"
    assert payload["families"]["active_code_analysis"]["status"] == "stable"
    assert payload["families"]["machine_analysis"]["status"] == "stable"
    assert payload["families"]["sinex"]["status"] == "stable"


def test_analysis_status_downgrades_machine_family_when_readiness_is_limited(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    analysis_root = repo_root / ".lynchpin/generated/analysis"
    analysis_root.mkdir(parents=True)
    for name in MACHINE_ANALYSIS_ARTIFACTS:
        (analysis_root / name).write_text('{"generated_at_utc":"2026-05-06T12:00:00+00:00"}', encoding="utf-8")
    (analysis_root / "machine_analysis_readiness.json").write_text(
        json.dumps({"dimensions": [{"dimension": "causal_support_gate", "status": "limited"}]}),
        encoding="utf-8",
    )

    spec_path = tmp_path / "analysis_spec.json"
    spec_path.write_text(json.dumps({}), encoding="utf-8")
    config = SimpleNamespace(
        analysis_output_dir=analysis_root,
        repo_root=repo_root,
        knowledgebase_root=tmp_path / "knowledgebase",
    )
    monkeypatch.setattr("lynchpin.analysis.core.status.get_config", lambda: config)

    payload = build_analysis_status(spec_path=spec_path)

    machine = payload["families"]["machine_analysis"]
    assert machine["status"] == "limited"
    assert "causal_support_gate=limited" in machine["rationale"]
