from datetime import datetime, timezone

import pytest

from lynchpin.graph.context_pack import (
    ContextPackSubstrateRequiredError,
    ContextPackSubstrateState,
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
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_episode_analysis.json").write_text(
        '{"episodes":[{"kind":"load_pressure","started_at":"2026-05-01T12:00:00+00:00","ended_at":"2026-05-01T12:01:00+00:00"}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_context_windows.json").write_text(
        '{"windows":[{"started_at":"2026-05-01T12:00:00+00:00","ended_at":"2026-05-01T12:02:00+00:00","projects":["sinity-lynchpin"],"episode_count":1}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_below_attribution.json").write_text(
        '{"pressure_episode_count":3,"unattributed_pressure_episode_count":2}',
        encoding="utf-8",
    )
    (analysis_root / "machine_work_state_windows.json").write_text(
        '{"window_count":2,"pressure_state_counts":{"io_pressure":1,"quiet":1},"work_state_counts":{"test_workload":1,"devshell_activation":1}}',
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
    (analysis_root / "devshell_performance.json").write_text(
        '{"command_count":2,"summaries":[{"command_class":"direnv_activation","command_count":1},{"command_class":"nix_develop","command_count":1}]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_baselines.json").write_text(
        '{"by_hardware_regime":[{"key":"gen4x16"}],"caveats":["observational"]}',
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_claims.json").write_text(
        '{"controlled_claim_count":0,"observational_claim_count":4}',
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_readiness.json").write_text(
        '{"dimensions":[{"dimension":"continuous_machine_telemetry","status":"stable"},{"dimension":"controlled_benchmark_claims","status":"missing"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lynchpin.analysis.core.io.get_config",
        lambda: type("Cfg", (), {"analysis_output_dir": analysis_root})(),
    )

    rendered = render_context_pack(context_pack(start=start, end=end, projects=("sinity-lynchpin",)))

    assert "## Machine Analysis" in rendered
    assert "Episodes in window: 1" in rendered
    assert "Work windows with machine episodes: 1/1" in rendered
    assert "Work-state segmentation: 2 windows" in rendered
    assert "Command performance: 3 commands" in rendered
    assert "Observational command deltas: 1 matched cohorts" in rendered
    assert "Devshell/Nix performance: 2 commands" in rendered
    assert "0 controlled / 4 observational" in rendered
    assert "Machine analysis readiness: missing×1, stable×1" in rendered


def test_context_pack_surfaces_missing_machine_analysis_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lynchpin.analysis.core.io.get_config",
        lambda: type("Cfg", (), {"analysis_output_dir": tmp_path / "analysis"})(),
    )

    rendered = _render_machine_analysis_artifacts(
        start=datetime(2026, 5, 1, tzinfo=UTC).date(),
        end=datetime(2026, 5, 2, tzinfo=UTC).date(),
        projects=("sinity-lynchpin",),
    )

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
