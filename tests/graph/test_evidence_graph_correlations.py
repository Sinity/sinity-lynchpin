from datetime import date, datetime, timezone
from types import SimpleNamespace
from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
from lynchpin.graph.evidence_graph import build_evidence_graph
from lynchpin.graph.evidence_views import render_evidence_graph_summary
from lynchpin.graph.work_correlation import dataset_correlations, render_dataset_correlations, render_supported_work_claims, supported_work_claims, work_day_correlations
from tests.graph.evidence_graph_fixtures import _mock_empty_sources, _no_analysis_claims, _no_substrate_overlap
UTC = timezone.utc

def test_work_day_correlations_projects_from_evidence_graph():
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    graph = EvidenceGraph(start=when.date(), end=when.date(), generated_at=when, nodes=(EvidenceNode(id='git:lynchpin:abc', kind='commit', source='git', date=when.date(), project='sinity-lynchpin', summary='feat: graph (#3)', start=when, end=when, payload={'commit': 'abc', 'github_refs': {'prs': [3], 'issues': []}}), EvidenceNode(id='polylogue:conv:sinity-lynchpin', kind='ai_session', source='polylogue', date=when.date(), project='sinity-lynchpin', summary='graph work', start=when, end=when, payload={'conversation_id': 'conv'}), EvidenceNode(id='aw:day', kind='focus_day', source='activitywatch', date=when.date(), project='sinity-lynchpin', summary='focus', payload={'duration_s': 1800})), edges=(EvidenceEdge(source_id='git:lynchpin:abc', target_id='polylogue:conv:sinity-lynchpin', relation='temporal_proximity', evidence='git within 0m of polylogue', weight=0.82),), caveats=())
    rows = work_day_correlations(start=when.date(), end=when.date(), graph=graph)
    claims = supported_work_claims(rows, graph=graph)
    rendered_claims = render_supported_work_claims(claims)
    assert len(rows) == 1
    assert rows[0].project == 'sinity-lynchpin'
    assert rows[0].commit_count == 1
    assert rows[0].ai_session_count == 1
    assert rows[0].focus_minutes == 30
    assert rows[0].github_refs == ('pr#3',)
    assert claims[0].support_level == 'strong'
    assert claims[0].relation_count == 1
    assert 'git within 0m of polylogue' in rendered_claims
    assert claims[0].strongest_edge_ids == ('git:lynchpin:abc->polylogue:conv:sinity-lynchpin:temporal_proximity',)

def test_dataset_correlations_summarize_cross_source_relation_support():
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    graph = EvidenceGraph(start=when.date(), end=when.date(), generated_at=when, nodes=(EvidenceNode(id='git:lynchpin:abc', kind='commit', source='git', date=when.date(), project='sinity-lynchpin', summary='feat: graph', start=when, end=when), EvidenceNode(id='polylogue:conv:sinity-lynchpin', kind='ai_session', source='polylogue', date=when.date(), project='sinity-lynchpin', summary='graph work', start=when, end=when)), edges=(EvidenceEdge(source_id='git:lynchpin:abc', target_id='polylogue:conv:sinity-lynchpin', relation='temporal_proximity', evidence='git within 0m of polylogue', weight=0.82),), caveats=())
    rows = dataset_correlations(graph)
    rendered = render_dataset_correlations(rows)
    assert rows[0].sources == ('git', 'polylogue')
    assert rows[0].relation_counts == {'temporal_proximity': 1}
    assert rows[0].projects == ('sinity-lynchpin',)
    assert 'git + polylogue' in rendered
    assert 'temporal_proximity=1' in rendered

def test_build_evidence_graph_links_temporal_proximity_across_sources(monkeypatch):
    commit_at = datetime(2026, 5, 5, 12, tzinfo=UTC)
    shell_at = datetime(2026, 5, 5, 12, 20, tzinfo=UTC)
    monkeypatch.setattr('lynchpin.graph.evidence_git.commit_facts', lambda **kwargs: (SimpleNamespace(repo='sinity-lynchpin', commit='abc123', authored_at=commit_at, author='Sinity', subject='fix: correlate nearby work', lines_added=3, lines_deleted=1, lines_changed=4, files_changed=1, paths=()),))
    monkeypatch.setattr('lynchpin.graph.evidence_polylogue.session_profiles_for_date', lambda **kwargs: ())
    monkeypatch.setattr('lynchpin.graph.evidence_polylogue.work_events', lambda **kwargs: ())
    monkeypatch.setattr('lynchpin.graph.evidence_raw_log.entries_in_range', lambda **kwargs: ())
    monkeypatch.setattr('lynchpin.graph.evidence_activitywatch.project_focus_days', lambda **kwargs: ())
    monkeypatch.setattr('lynchpin.graph.evidence_terminal.shell_sessions', lambda **kwargs: (SimpleNamespace(start=shell_at, end=datetime(2026, 5, 5, 12, 25, tzinfo=UTC), project='sinity-lynchpin', cwd='/realm/project/sinity-lynchpin', duration_s=300, command_count=2, error_count=0, category='coding', commands_summary=('pytest', 'git')),))
    monkeypatch.setattr('lynchpin.graph.evidence_system_signals.add_temporal_signals', lambda nodes, **kwargs: None)
    monkeypatch.setattr('lynchpin.graph.evidence_system_signals.add_readiness', lambda nodes, **kwargs: None)
    monkeypatch.setattr('lynchpin.graph.evidence_analysis.latest_artifacts', lambda **kwargs: ())
    monkeypatch.setattr('lynchpin.graph.evidence_graph.source_readiness', lambda **kwargs: SimpleNamespace(caveats=()))
    graph = build_evidence_graph(start=date(2026, 5, 5), end=date(2026, 5, 5))
    assert any((edge.relation == 'temporal_proximity' and 'within 20m' in edge.evidence for edge in graph.edges))
    assert 'temporal_proximity=1' in render_evidence_graph_summary(graph)
