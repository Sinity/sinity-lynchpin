from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.graph.context_pack import (
    ContextPackSubstrateRequiredError,
    ContextPackSubstrateState,
    _render_content_metadata_coverage,
    _render_machine_analysis_artifacts,
    context_pack,
    graph_context_pack,
    render_context_pack,
)
from lynchpin.graph.current_state import CurrentStateEvidencePack
from lynchpin.core.evidence import EvidenceCaveat, SourceReadiness, SourceReadinessReport
from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode
from lynchpin.graph.movement import movement_summary, render_movement_summary
from lynchpin.graph.work_correlation import CorrelatedWorkDay, WorkCorrelationSummary
from lynchpin.sources.polylogue import PolylogueReadiness


UTC = timezone.utc


def test_content_metadata_coverage_converges_activity_content(monkeypatch) -> None:
    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: ensure_calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda *, start, end, ensure=True: [
            SimpleNamespace(
                focused_seconds=3600.0,
                matched_seconds=2700.0,
                gpt_matched_seconds=900.0,
                activity_seconds={"coding": 3000.0, "reading": 600.0},
                topic_seconds={"lynchpin": 3000.0},
                content_type_seconds={"code": 3000.0},
            )
        ],
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_title_usage",
        lambda *, start, end, ensure=True: (),
    )

    rendered = _render_content_metadata_coverage(
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )

    assert ensure_calls == [
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 3)))
    ]
    assert "Title metadata coverage: 75.0%" in rendered


def _row(project="sinity-lynchpin"):
    return CorrelatedWorkDay(
        date=datetime(2026, 5, 5, tzinfo=UTC).date(),
        project=project,
        commit_count=2,
        commit_shas=("a", "b"),
        commit_subjects=("feat: one", "fix: two"),
        github_refs=("issue#1",),
        github_lifecycles={"executed": 1},
        ai_session_count=1,
        ai_conversation_ids=("conv-1",),
        raw_log_count=1,
        raw_log_refs=("logs.raw-log.md:1",),
        focus_minutes=90,
        shell_minutes=10,
        shell_command_count=4,
        sources=("activitywatch", "git", "github", "polylogue", "raw_log", "terminal"),
    )


def test_movement_summary_keeps_dimensions_separate():
    summary = movement_summary(
        start=datetime(2026, 5, 1).date(),
        end=datetime(2026, 5, 6).date(),
        rows=(_row(),),
    )

    project = summary.projects[0]
    assert project.commits == 2
    assert project.ai_sessions == 1
    assert project.focus_hours == 1.5
    assert project.github_refs == 1
    assert project.lifecycle_counts == {"executed": 1}
    assert any("Commit count varies" in caveat.message for caveat in project.caveats)
    assert "Movement Summary" not in render_movement_summary(summary)
    assert "sinity-lynchpin" in render_movement_summary(summary)


def test_context_pack_filters_projects_and_renders_caveats(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 6, tzinfo=UTC)
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="degraded",
        reason="session-profile products are stale",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=0,
        day_summary_count=0,
        work_event_count=0,
        provider_event_count=None,
        derives_profiles_from_base_tables=True,
        derives_day_summaries_from_profiles=True,
    )
    rows = (_row("sinity-lynchpin"), _row("polylogue"))
    pack = CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=start,
        inventory=(),
        polylogue_readiness=readiness,
        evidence_graph=EvidenceGraph(
            start=start.date(),
            end=end.date(),
            generated_at=start,
            mode="materialized",
            nodes=(
                EvidenceNode(
                    id="git:sinity-lynchpin:a",
                    kind="commit",
                    source="git",
                    date=start.date(),
                    project="sinity-lynchpin",
                    start=start,
                    end=start,
                    summary="feat: context timeline",
                ),
            ),
            edges=(),
            caveats=(),
        ),
        source_readiness=SourceReadinessReport(start=start.date(), end=end.date(), generated_at=start, sources=()),
        work_correlations=rows,
        correlation_summary=WorkCorrelationSummary(
            row_count=2,
            cross_source_row_count=2,
            projects=("polylogue", "sinity-lynchpin"),
            source_counts={},
            source_pair_counts={},
            git_without_ai_or_focus=0,
            ai_without_git=0,
            focus_without_git=0,
            terminal_without_git=0,
        ),
        movement=movement_summary(start=start.date(), end=end.date(), rows=rows),
        github_frontiers=(),
    )

    monkeypatch.setattr("lynchpin.graph.context_pack.current_state_evidence_pack", lambda **kwargs: pack)
    monkeypatch.setattr(
        "lynchpin.graph.context_pack.build_evidence_graph",
        lambda **kwargs: pack.evidence_graph,
    )

    context = context_pack(start=start, end=end, projects=("lynchpin",))
    rendered = render_context_pack(context)

    assert [project.project for project in context.projects] == ["sinity-lynchpin"]
    assert "## Chronological Evidence" in rendered
    assert "## Graph Relations" in rendered
    assert "## Dataset Correlations" in rendered
    assert "## Supported Work Claims" in rendered
    assert "feat: context timeline" in rendered
    assert "polylogue |" in rendered


def test_context_pack_renders_machine_analysis_artifacts(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)
    rows = (_row("sinity-lynchpin"),)
    pack = CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=start,
        inventory=(),
        polylogue_readiness=PolylogueReadiness(
            db_path=tmp_path / "polylogue.db",
            status="ready",
            reason="ready",
            conversation_count=1,
            message_count=None,
            conversation_stats_count=1,
            session_profile_count=1,
            day_summary_count=1,
            work_event_count=1,
            provider_event_count=None,
            derives_profiles_from_base_tables=False,
            derives_day_summaries_from_profiles=False,
        ),
        evidence_graph=EvidenceGraph(
            start=start.date(),
            end=end.date(),
            generated_at=start,
            mode="materialized",
            nodes=(),
            edges=(),
            caveats=(),
        ),
        source_readiness=SourceReadinessReport(start=start.date(), end=end.date(), generated_at=start, sources=()),
        work_correlations=rows,
        correlation_summary=WorkCorrelationSummary(
            row_count=1,
            cross_source_row_count=1,
            projects=("sinity-lynchpin",),
            source_counts={},
            source_pair_counts={},
            git_without_ai_or_focus=0,
            ai_without_git=0,
            focus_without_git=0,
            terminal_without_git=0,
        ),
        movement=movement_summary(start=start.date(), end=end.date(), rows=rows),
        github_frontiers=(),
    )
    monkeypatch.setattr("lynchpin.graph.context_pack.current_state_evidence_pack", lambda **kwargs: pack)
    monkeypatch.setattr("lynchpin.graph.context_pack.build_evidence_graph", lambda **kwargs: pack.evidence_graph)
    ensured: list[str] = []

    def fake_ensure_materialized(name: str, **_kwargs):
        ensured.append(name)
        return type("Result", (), {"to_json": lambda self: {"status": "ready"}})()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_telemetry_analysis.json").write_text(
        '{"coverage":{"sample_count":10,"first_observed_at":"2026-05-01T00:00:00+00:00","last_observed_at":"2026-05-02T00:00:00+00:00"},"hardware_regimes":[{}],"signals":[{},{}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_episode_analysis.json").write_text(
        '{"episodes":[{"kind":"load_pressure","started_at":"2026-05-01T12:00:00+00:00","ended_at":"2026-05-01T12:01:00+00:00"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_context_windows.json").write_text(
        '{"windows":[{"started_at":"2026-05-01T12:00:00+00:00","ended_at":"2026-05-01T12:02:00+00:00","projects":["sinity-lynchpin"],"episode_count":1}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_below_attribution.json").write_text(
        '{"pressure_episode_count":3,"attributed_episode_count":0,"workload_resource_attributed_pressure_episode_count":1,"residual_unattributed_pressure_episode_count":2}',
        encoding="utf-8",
    )
    (analysis_root / "machine_below_analysis.json").write_text(
        '{"window_count":4,"top_process_count":5,"top_cgroup_count":6,"live_store":{"index_count":7}}',
        encoding="utf-8",
    )
    (analysis_root / "machine_below_export_handoff.json").write_text(
        '{"planned_window_count":2,"failed_capture_count":1,"root":"/realm/data/captures/stability-lab","items":[{"episode_kind":"io_pressure"},{"episode_kind":"load_pressure"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_work_state_windows.json").write_text(
        '{"window_count":2,"pressure_state_counts":{"io_pressure":1,"quiet":1},"work_state_counts":{"test_workload":1,"devshell_activation":1}}',
        encoding="utf-8",
    )
    (analysis_root / "machine_work_observations.json").write_text(
        '{"daily":[{"date":"2026-05-01","project":"sinity-lynchpin","command":["pytest"],"observation_count":1}],"sinex_check_daily":[],"stage_summaries":[{"stage_name":"pytest"}],"test_summaries":[{"package":"lynchpin"}]}',
        encoding="utf-8",
    )
    (analysis_root / "command_performance_windows.json").write_text(
        '{"command_count":3,"tools":[{"tool":"pytest","command_count":2,"pressure_overlap_count":1},{"tool":"direnv","command_count":1,"pressure_overlap_count":0}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_deltas.json").write_text(
        '{"deltas":[{"tool":"pytest","work_state":"test_workload","pressure_state":"io_pressure","median_delta_seconds":4.2}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_candidates.json").write_text(
        '{"pareto_frontier_count":1,"pareto_frontier_ids":["c1"],"candidates":[{"candidate_id":"c1","metric":"command.pytest.duration_seconds","mechanism_family":"stage_regression_or_workload_mix","priority_score":8.4,"validation_status":"design_ready","pareto_frontier":true}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_feature_frames.json").write_text(
        '{"frame":{"row_count":3525}}',
        encoding="utf-8",
    )
    (analysis_root / "machine_mining.json").write_text(
        '{"cohort_count":48}',
        encoding="utf-8",
    )
    (analysis_root / "machine_dataset_diagnostics.json").write_text(
        '{"feature_audit":{"status":"ready_for_mining"},"mining_audit":{"multiplicity_status":"registered"}}',
        encoding="utf-8",
    )
    (analysis_root / "machine_validation_design.json").write_text(
        '{"boundary_count":12}',
        encoding="utf-8",
    )
    (analysis_root / "machine_matched_designs.json").write_text(
        '{"design_count":9}',
        encoding="utf-8",
    )
    (analysis_root / "machine_comparisons.json").write_text(
        '{"contrast_count":31}',
        encoding="utf-8",
    )
    (analysis_root / "machine_derivation_inventory.json").write_text(
        '{"ready_target_count":7,"targets":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_plans.json").write_text(
        '{"ready_plan_count":10,"plans":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_manifest_bundle.json").write_text(
        '{"run_template_count":120,"groups":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_preflight.json").write_text(
        '{"ready_run_count":120,"run_count":120}',
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_execution_handoff.json").write_text(
        '{"handoff_count":10,"ready_group_count":10,"blocked_group_count":0,"run_template_count":120,"ready_run_count":120}',
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_manifest_diagnostics.json").write_text(
        '{"controlled_benchmark_valid_count":0,"ad_hoc_observational_count":55,"diagnostics":[]}',
        encoding="utf-8",
    )
    (analysis_root / "devshell_performance.json").write_text(
        '{"command_count":2,"summaries":[{"command_class":"direnv_activation","command_count":1},{"command_class":"nix_develop","command_count":1}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_baselines.json").write_text(
        '{"by_hardware_regime":[{"key":"gen4x16"}],"caveats":["observational"]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_claims.json").write_text(
        '{"controlled_claim_count":0,"observational_claim_count":4,"effect_estimates":[{"run_group_id":"grp1","estimator":"stratified_bootstrap_mean_delta","delta":-2.5,"ci_low":-4.0,"ci_high":-1.0,"p_value":0.125,"p_value_method":"exact_stratified_label_permutation_two_sided"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_claims.json").write_text(
        '{"claim_count":25,"by_support_level":{"insufficient":25},"claims":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_mechanism_hypotheses.json").write_text(
        '{"mechanism_count":2,"mechanisms":[{"mechanism_family":"resource_contention"},{"mechanism_family":"stage_regression_or_workload_mix"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_instrumentation_gaps.json").write_text(
        '{"gap_count":3,"by_missing_source":{"controlled_benchmark_run":2,"nix_internal_json":1},"gaps":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_negative_controls.json").write_text(
        '{"control_count":8,"by_status":{"passed":6,"failed":2},"controls":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_assumption_checks.json").write_text(
        '{"check_count":25,"by_status":{"failed":25},"checks":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_calibration_fixtures.json").write_text(
        '{"fixture_count":8,"by_status":{"passed":8},"fixtures":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_measurement_system.json").write_text(
        '{"check_count":5,"by_status":{"passed":3,"untestable":2},"checks":[]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_support_assessment.json").write_text(
        '{"assessment_count":25,"refusal_count":13,"natural_experiment_support_count":12,"ready_plan_count":10,"run_template_count":120,"controlled_claim_count":0,"assessments":[{"claim_id":"c1","support_level":"insufficient","refusal_reasons":["missing controlled benchmark run"],"instrumentation_gaps":[{"missing_source":"controlled_benchmark_run","next_action":"execute the approved manifest and promote run logs/telemetry"}]},{"claim_id":"c2","support_level":"natural_experiment","refusal_reasons":[],"instrumentation_gaps":[]}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_readiness.json").write_text(
        '{"dimensions":[{"dimension":"continuous_machine_telemetry","status":"stable"},{"dimension":"controlled_benchmark_claims","status":"missing"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_gap_summary.json").write_text(
        '{"generated_for":{"window_start":"2026-05-01T00:00:00+00:00","window_end":"2026-05-02T00:00:00+00:00"},"counts":[{}],"regressions":[{},{}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_materialization_report.json").write_text(
        '{"step_count":31,"by_status":{"success":31},"steps":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lynchpin.core.io.get_config",
        lambda: type("Cfg", (), {"analysis_output_dir": analysis_root})(),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda **_kwargs: iter(()),
    )

    rendered = render_context_pack(context_pack(start=start, end=end, projects=("sinity-lynchpin",)))

    assert ensured.count("personal_daily_signals") == 1
    assert "activity_content" in ensured
    assert "analysis_artifacts" in ensured
    assert "## Machine Analysis" in rendered
    assert "Telemetry coverage: samples=10; span=2026-05-01T00:00:00+00:00..2026-05-02T00:00:00+00:00; hardware_regimes=1; signals=2" in rendered
    assert "Episodes in window: 1" in rendered
    assert "Work windows with machine episodes: 1/1" in rendered
    assert "Work-state segmentation: 2 windows" in rendered
    assert "Work observations: 1 daily groups" in rendered
    assert "Process attribution: bounded_below=0/3; workload_resource=1/3; residual_unattributed=2" in rendered
    assert "Below analysis coverage: bounded_windows=4; top_processes=5; top_cgroups=6; live_store_indexes=7" in rendered
    assert "Below export handoff: 2 planned windows; failed=1; kinds=io_pressure×1, load_pressure×1; root=/realm/data/captures/stability-lab" in rendered
    assert "Command performance: 3 commands" in rendered
    assert "Observational command deltas: 1 matched cohorts" in rendered
    assert "Attribution candidates: 1 non-causal candidates; frontier=1; validation=design_ready×1; families=stage_regression_or_workload_mix×1; top=command.pytest.duration_seconds" in rendered
    assert "Dataset mining infra: feature_rows=3525; feature_status=ready_for_mining; multiplicity=registered; cohorts=48; boundaries=12; matched_designs=9; contrasts=31" in rendered
    assert "Controlled benchmark infra: derivations=7; ready_plans=10; run_templates=120; preflight_ready=120; handoff_ready=10/10; executed_valid=0; ad_hoc_observational=55" in rendered
    assert "Devshell/Nix performance: 2 commands" in rendered
    assert "0 controlled / 4 observational" in rendered
    assert "estimates=1; top=grp1; estimator=stratified_bootstrap_mean_delta; delta=-2.5; ci95=[-4.0, -1.0]; p=0.125; p_method=exact_stratified_label_permutation_two_sided" in rendered
    assert "Attribution claim ledger: 25 claims; insufficient×25" in rendered
    assert "Mechanism hypotheses: 2 families; resource_contention×1, stage_regression_or_workload_mix×1" in rendered
    assert "Instrumentation gaps: 3 gaps; controlled_benchmark_run×2, nix_internal_json×1" in rendered
    assert "Negative controls: 8 checks; passed×6, failed×2" in rendered
    assert "Assumption checks: 25 checks; failed×25" in rendered
    assert "Calibration fixtures: 8 fixtures; passed×8" in rendered
    assert "Measurement system: 5 checks; passed×3, untestable×2" in rendered
    assert "Causal support gate: 13/25 refused; support=insufficient×1, natural_experiment×1; top_refusal=missing controlled benchmark run×1; next=execute the approved manifest and promote run logs/telemetry×1; ready_plans=10; run_templates=120; controlled_claims=0" in rendered
    assert "Machine analysis readiness: missing×1, stable×1" in rendered
    assert "Machine capture gaps: counts=1; regressions=2; window=2026-05-01T00:00:00+00:00..2026-05-02T00:00:00+00:00" in rendered
    assert "Machine materialization report: 31 steps; success×31" in rendered


def test_context_pack_surfaces_missing_machine_analysis_artifacts(monkeypatch, tmp_path):
    ensured: list[str] = []

    def fake_ensure_materialized(name: str, *, cfg):
        ensured.append(name)
        return type("Result", (), {"to_json": lambda self: {"status": "ready"}})()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.core.io.get_config",
        lambda: type("Cfg", (), {"analysis_output_dir": tmp_path / "analysis"})(),
    )

    rendered = _render_machine_analysis_artifacts(
        start=datetime(2026, 5, 1, tzinfo=UTC).date(),
        end=datetime(2026, 5, 2, tzinfo=UTC).date(),
        projects=("sinity-lynchpin",),
    )

    assert ensured == ["analysis_artifacts"]
    assert "Missing machine analysis artifacts:" in rendered
    assert "machine_episode_analysis.json" in rendered


def test_graph_context_pack_dedupes_overlapping_caveats(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 6, tzinfo=UTC)
    repeated = EvidenceCaveat("polylogue", "partial", "derived profile products")
    graph = EvidenceGraph(
        start=start.date(),
        end=end.date(),
        generated_at=start,
        mode="materialized",
        nodes=(),
        edges=(),
        caveats=(repeated,),
    )
    pack = CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=start,
        inventory=(),
        polylogue_readiness=PolylogueReadiness(
            db_path=tmp_path / "polylogue.db",
            status="degraded",
            reason="session-profile products are stale",
            conversation_count=1,
            message_count=None,
            conversation_stats_count=1,
            session_profile_count=0,
            day_summary_count=0,
            work_event_count=0,
            provider_event_count=None,
            derives_profiles_from_base_tables=True,
            derives_day_summaries_from_profiles=True,
        ),
        evidence_graph=graph,
        source_readiness=SourceReadinessReport(
            start=start.date(),
            end=end.date(),
            generated_at=start,
            sources=(
                SourceReadiness(
                    source="polylogue",
                    status="partial",
                    reason="degraded",
                    cost="materialized",
                    caveats=(repeated.message,),
                ),
            ),
        ),
        work_correlations=(),
        correlation_summary=WorkCorrelationSummary(
            row_count=0,
            cross_source_row_count=0,
            projects=(),
            source_counts={},
            source_pair_counts={},
            git_without_ai_or_focus=0,
            ai_without_git=0,
            focus_without_git=0,
            terminal_without_git=0,
        ),
        movement=movement_summary(start=start.date(), end=end.date(), rows=()),
        github_frontiers=(),
    )
    monkeypatch.setattr("lynchpin.graph.context_pack.current_state_evidence_pack", lambda **kwargs: pack)

    context = graph_context_pack(graph, start=start, end=end)

    assert context.caveats.count(repeated) == 1


def test_context_pack_can_include_weak_tags(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 6, tzinfo=UTC)
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="available",
        reason="ok",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=1,
        day_summary_count=1,
        work_event_count=1,
        provider_event_count=1,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )
    graph = EvidenceGraph(
        start=start.date(),
        end=end.date(),
        generated_at=start,
        mode="materialized",
        nodes=(),
        edges=(),
        caveats=(),
    )
    pack = CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=start,
        inventory=(),
        polylogue_readiness=readiness,
        evidence_graph=graph,
        source_readiness=SourceReadinessReport(start=start.date(), end=end.date(), generated_at=start, sources=()),
        work_correlations=(),
        correlation_summary=WorkCorrelationSummary(
            row_count=0,
            cross_source_row_count=0,
            projects=(),
            source_counts={},
            source_pair_counts={},
            git_without_ai_or_focus=0,
            ai_without_git=0,
            focus_without_git=0,
            terminal_without_git=0,
        ),
        movement=movement_summary(start=start.date(), end=end.date(), rows=()),
        github_frontiers=(),
    )

    monkeypatch.setattr("lynchpin.graph.context_pack.current_state_evidence_pack", lambda **kwargs: pack)
    monkeypatch.setattr("lynchpin.graph.context_pack.build_evidence_graph", lambda **kwargs: graph)

    rendered = render_context_pack(context_pack(start=start, end=end, weak_tags=True))

    assert "## Weak Evidence Tags" in rendered
    assert "Narrative moments" in rendered


def test_context_pack_records_exact_substrate_hit(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)
    graph = EvidenceGraph(
        start=start.date(),
        end=end.date(),
        generated_at=start,
        mode="materialized",
        nodes=(),
        edges=(),
        caveats=(),
    )
    state = ContextPackSubstrateState(
        status="exact_hit",
        refresh_id="current-state:2026-05-01:2026-05-02:materialized:all",
        message="Loaded exact materialized DuckDB graph.",
    )
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="ready",
        reason="ready",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=1,
        day_summary_count=1,
        work_event_count=1,
        provider_event_count=None,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )
    pack = CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=start,
        inventory=(),
        polylogue_readiness=readiness,
        evidence_graph=graph,
        source_readiness=SourceReadinessReport(start=start.date(), end=end.date(), generated_at=start, sources=()),
        work_correlations=(),
        correlation_summary=WorkCorrelationSummary(
            row_count=0,
            cross_source_row_count=0,
            projects=(),
            source_counts={},
            source_pair_counts={},
            git_without_ai_or_focus=0,
            ai_without_git=0,
            focus_without_git=0,
            terminal_without_git=0,
        ),
        movement=movement_summary(start=start.date(), end=end.date(), rows=()),
        github_frontiers=(),
    )

    monkeypatch.setattr("lynchpin.graph.context_pack._load_substrate_graph", lambda **kwargs: (graph, None, state))
    monkeypatch.setattr("lynchpin.graph.context_pack.current_state_evidence_pack", lambda **kwargs: pack)
    monkeypatch.setattr(
        "lynchpin.graph.context_pack.build_evidence_graph",
        lambda **kwargs: pytest.fail("exact substrate hit should not rebuild live graph"),
    )

    context = context_pack(start=start, end=end, prefer_substrate=True)

    assert context.substrate_state.status == "exact_hit"
    assert "Substrate graph: `exact_hit`" in render_context_pack(context)


def test_context_pack_requires_materialized_substrate_by_default(monkeypatch):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)
    state = ContextPackSubstrateState(
        status="missing",
        refresh_id="current-state:2026-05-01:2026-05-02:all",
        message="No materialized DuckDB graph matched.",
    )

    monkeypatch.setattr(
        "lynchpin.graph.context_pack._load_substrate_graph",
        lambda **kwargs: (None, EvidenceCaveat("substrate", "partial", state.message), state),
    )

    with pytest.raises(ContextPackSubstrateRequiredError):
        context_pack(
            start=start,
            end=end,
            prefer_substrate=True,
        )
