import json
from datetime import date, datetime, timezone
import pytest
from lynchpin.core.evidence_graph import EvidenceNode
from lynchpin.graph.evidence_edges import load_symbol_changes_index, mentions_project_edges, same_project_day_edges, temporal_overlap_edges

def _reload_config(monkeypatch) -> None:
    import lynchpin.core.config as cfg_mod
    monkeypatch.setattr(cfg_mod, '_CONFIG', None, raising=False)

def test_load_symbol_changes_index_requires_materialized_product(tmp_path, monkeypatch):
    monkeypatch.setenv('LYNCHPIN_ANALYSIS_OUTPUT_DIR', str(tmp_path / 'analysis'))
    _reload_config(monkeypatch)
    with pytest.raises(FileNotFoundError, match='active symbol-change product'):
        load_symbol_changes_index()

def test_load_symbol_changes_index_accepts_valid_empty_product(tmp_path, monkeypatch):
    monkeypatch.setenv('LYNCHPIN_ANALYSIS_OUTPUT_DIR', str(tmp_path / 'analysis'))
    _reload_config(monkeypatch)
    analysis_dir = tmp_path / 'analysis'
    analysis_dir.mkdir(parents=True)
    (analysis_dir / 'active_symbol_changes.json').write_text(json.dumps({'events': []}), encoding='utf-8')
    assert load_symbol_changes_index() == {}

def test_mentions_project_edges_skips_no_matches() -> None:
    day = date(2026, 5, 24)
    commit = EvidenceNode(id='git:sinex:abc123', kind='commit', source='git', date=day, project='sinex', summary='feat: implement parser')
    edges = mentions_project_edges([seed, commit])
    assert len(edges) == 0

def test_same_project_day_edges_emits_only_cross_source_pairs() -> None:
    day = date(2026, 5, 24)
    project = 'test-project'

    def node(node_id: str, source: str) -> EvidenceNode:
        return EvidenceNode(id=node_id, kind='commit' if source == 'git' else 'github_ref' if source == 'github' else 'raw_log', source=source, date=day, project=project, summary=node_id)
    edges = same_project_day_edges(nodes)
    assert len(edges) <= 3, f'expected ≤3 edges, got {len(edges)}'
    edge_pairs = {(e.source_id.split('-')[0], e.target_id.split('-')[0]) for e in edges}
    for source_a, source_b in edge_pairs:
        assert source_a != source_b, f'same-source edge found: {source_a} ↔ {source_b}'

def test_temporal_overlap_edges_group_by_project_and_stop_at_interval_end() -> None:
    day = date(2026, 5, 24)

    def node(node_id: str, source: str, project: str, start_minute: int, end_minute: int) -> EvidenceNode:
        return EvidenceNode(id=node_id, kind='terminal_session', source=source, date=day, project=project, summary=node_id, start=datetime(2026, 5, 24, 10, start_minute, tzinfo=timezone.utc), end=datetime(2026, 5, 24, 10, end_minute, tzinfo=timezone.utc))
    edges = temporal_overlap_edges([node('git-a', 'git', 'lynchpin', 0, 20), node('ai-a', 'polylogue', 'lynchpin', 10, 25), node('term-a', 'terminal', 'lynchpin', 25, 30), node('git-b', 'git', 'polylogue', 10, 15), node('ai-b', 'polylogue', 'polylogue', 16, 20)])
    assert [(edge.source_id, edge.target_id, edge.relation) for edge in edges] == [('git-a', 'ai-a', 'temporal_overlap')]

def test_temporal_overlap_admits_point_event_inside_interval() -> None:
    """Commits have start == end (point events). A point inside an interval
    is a legitimate overlap — previously dropped by the `end > start` filter,
    making every commit invisible to this layer."""
    day = date(2026, 5, 24)
    commit_at = datetime(2026, 5, 24, 10, 15, tzinfo=timezone.utc)
    session_start = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    session_end = datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc)
    nodes = [EvidenceNode(id='commit-x', kind='commit', source='git', date=day, project='lynchpin', summary='x', start=commit_at, end=commit_at), EvidenceNode(id='ai-x', kind='ai_session', source='polylogue', date=day, project='lynchpin', summary='x', start=session_start, end=session_end)]
    edges = temporal_overlap_edges(nodes)
    rels = {(e.source_id, e.target_id, e.relation) for e in edges}
    assert ('commit-x', 'ai-x', 'temporal_overlap') in rels or ('ai-x', 'commit-x', 'temporal_overlap') in rels

def test_temporal_overlap_excludes_point_outside_interval() -> None:
    """Negative case: commit BEFORE an AI session starts must NOT overlap."""
    day = date(2026, 5, 24)
    commit_at = datetime(2026, 5, 24, 9, 50, tzinfo=timezone.utc)
    session_start = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    session_end = datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc)
    nodes = [EvidenceNode(id='commit-y', kind='commit', source='git', date=day, project='lynchpin', summary='y', start=commit_at, end=commit_at), EvidenceNode(id='ai-y', kind='ai_session', source='polylogue', date=day, project='lynchpin', summary='y', start=session_start, end=session_end)]
    edges = temporal_overlap_edges(nodes)
    assert edges == ()
