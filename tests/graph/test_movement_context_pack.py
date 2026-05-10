from datetime import datetime, timezone

from lynchpin.graph.context_pack import context_pack, graph_context_pack, render_context_pack
from lynchpin.graph.current_state import CurrentStateEvidencePack
from lynchpin.graph.evidence import EvidenceCaveat, SourceReadiness, SourceReadinessReport
from lynchpin.graph.evidence_graph import EvidenceGraph, EvidenceNode
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
        reason="base archive usable",
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
            mode="local-fast",
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

    context = context_pack(start=start, end=end, projects=("lynchpin",), mode="local-fast")
    rendered = render_context_pack(context)

    assert [project.project for project in context.projects] == ["sinity-lynchpin"]
    assert "## Chronological Evidence" in rendered
    assert "## Graph Relations" in rendered
    assert "## Dataset Correlations" in rendered
    assert "## Supported Work Claims" in rendered
    assert "feat: context timeline" in rendered
    assert "polylogue |" in rendered


def test_graph_context_pack_dedupes_overlapping_caveats(monkeypatch, tmp_path):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 6, tzinfo=UTC)
    repeated = EvidenceCaveat("polylogue", "partial", "derived profile products")
    graph = EvidenceGraph(
        start=start.date(),
        end=end.date(),
        generated_at=start,
        mode="local-fast",
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
            reason="base archive usable",
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
                    cost="local-fast",
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


def test_context_pack_can_include_semantic_enrichment(monkeypatch, tmp_path):
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
        mode="local-fast",
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

    rendered = render_context_pack(context_pack(start=start, end=end, semantic=True))

    assert "## Semantic Enrichment" in rendered
    assert "Narrative moments" in rendered
