from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.graph.evidence_graph import build_evidence_graph
from tests.graph.evidence_graph_fixtures import (  # noqa: F401
    _mock_empty_sources,
    _no_analysis_claims,
)

UTC = timezone.utc


def test_build_evidence_graph_surfaces_analysis_artifacts(monkeypatch, tmp_path):
    generated = datetime(2026, 5, 4, 10, tzinfo=UTC)
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifact = SimpleNamespace(
        name="sinex_structure_metrics.json",
        path=tmp_path / "sinex_structure_metrics.json",
        kind="json",
        project="sinex",
        projects=("sinex",),
        size_bytes=42,
        modified_at=modified,
        generated_at=generated,
        top_level_keys=("generated_at_utc", "totals"),
        brief="sinex summary",
        references=(),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts",
        lambda **kwargs: (artifact,),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinex",),
    )

    analysis_nodes = [n for n in graph.nodes if n.kind == "analysis_artifact"]
    assert len(analysis_nodes) == 1
    node = analysis_nodes[0]
    assert node.source == "analysis"
    assert node.project == "sinex"
    assert node.date == date(2026, 5, 5)
    assert node.payload["top_level_keys"] == ("generated_at_utc", "totals")
    assert node.payload["brief"] == "sinex summary"
    assert node.provenance.path.endswith("sinex_structure_metrics.json")


def test_build_evidence_graph_excludes_named_analysis_artifacts(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifacts = (
        SimpleNamespace(
            name="current_state_context_pack.json",
            path=tmp_path / "current_state_context_pack.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("graph",),
            brief="previous current-state pack",
            references=(),
        ),
        SimpleNamespace(
            name="analysis_status.json",
            path=tmp_path / "analysis_status.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("families",),
            brief="status",
            references=("current_state_context_pack.json",),
        ),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: artifacts
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinity-lynchpin",),
        exclude_analysis_artifacts=("current_state_context_pack.json",),
    )

    names = {
        node.payload["name"]
        for node in graph.nodes
        if node.kind == "analysis_artifact" and node.payload
    }
    assert names == {"analysis_status.json"}
    assert not graph.edges


def test_build_evidence_graph_surfaces_analysis_claims(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifact = SimpleNamespace(
        name="active_project_snapshot.json",
        path=tmp_path / "active_project_snapshot.json",
        kind="json",
        project="sinex",
        projects=("sinex",),
        size_bytes=42,
        modified_at=modified,
        generated_at=modified,
        top_level_keys=("projects",),
        brief="snapshot",
        references=(),
    )
    claim = SimpleNamespace(
        id="active-project-snapshot:sinex",
        artifact_name="active_project_snapshot.json",
        claim_type="project_snapshot",
        project="sinex",
        summary="sinex: 3 first-parent commits",
        payload={"recent_git": {"commit_count": 3}},
        confidence=0.82,
        generated_at=modified,
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts",
        lambda **kwargs: (artifact,),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.analysis_claims", lambda **kwargs: (claim,)
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinex",),
    )

    claim_node = next(node for node in graph.nodes if node.kind == "analysis_claim")
    assert claim_node.summary == "sinex: 3 first-parent commits"
    assert claim_node.payload["claim_type"] == "project_snapshot"
    assert any(
        edge.source_id == "analysis-claim:active-project-snapshot:sinex"
        and edge.target_id == "analysis:active_project_snapshot.json:sinex"
        and edge.relation == "references"
        for edge in graph.edges
    )


def test_build_evidence_graph_links_analysis_artifact_references(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifacts = (
        SimpleNamespace(
            name="analysis_status.json",
            path=tmp_path / "analysis_status.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("families",),
            brief="status",
            references=("cross_project_metrics.json",),
        ),
        SimpleNamespace(
            name="cross_project_metrics.json",
            path=tmp_path / "cross_project_metrics.json",
            kind="json",
            project=None,
            projects=("sinex", "polylogue", "sinity-lynchpin"),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("projects",),
            brief="metrics",
            references=(),
        ),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: artifacts
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinity-lynchpin",),
    )

    assert any(
        edge.source_id == "analysis:analysis_status.json:sinity-lynchpin"
        and edge.target_id == "analysis:cross_project_metrics.json:sinity-lynchpin"
        and edge.relation == "references"
        for edge in graph.edges
    )
