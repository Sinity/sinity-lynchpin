import pytest

@pytest.fixture(autouse=True)
def _no_analysis_claims(monkeypatch):
    monkeypatch.setattr('lynchpin.graph.evidence_analysis.analysis_claims', lambda **kwargs: ())

@pytest.fixture(autouse=True)
def _no_substrate_overlap(monkeypatch):
    """Skip SQL overlap path in unit tests — tested by test_substrate_views.py."""
    monkeypatch.setattr('lynchpin.graph.evidence_edges.overlap_edges_via_substrate', lambda nodes, **kwargs: ())

@pytest.fixture(autouse=True)
def _mock_empty_sources(monkeypatch, tmp_path):
    """Default all source functions to return empty — individual tests
    override with their own monkeypatch.setattr calls as needed.

    Evidence graph construction calls source-family modules directly, so tests
    patch those owners instead of raw source modules. Individual tests override
    these defaults for the sources they need.
    """
    aw = 'lynchpin.graph.evidence_activitywatch'
    git = 'lynchpin.graph.evidence_git'
    polylogue = 'lynchpin.graph.evidence_polylogue'
    raw_log = 'lynchpin.graph.evidence_raw_log'
    system = 'lynchpin.graph.evidence_system_signals'
    terminal = 'lynchpin.graph.evidence_terminal'
    web = 'lynchpin.graph.evidence_web_media'

    def empty(*args, **kwargs):
        return ()

    def empty_list(*args, **kwargs):
        return []
    monkeypatch.setattr(f'{git}.commit_facts', empty)
    monkeypatch.setattr(f'{git}.github_context_for_commits', empty)
    monkeypatch.setattr(f'{polylogue}.session_profiles_for_date', empty)
    monkeypatch.setattr(f'{polylogue}.work_events', empty)
    monkeypatch.setattr(f'{raw_log}.entries_in_range', empty)
    monkeypatch.setattr(f'{aw}.project_focus_days', empty_list)
    monkeypatch.setattr(f'{aw}.deep_work', empty_list)
    monkeypatch.setattr(f'{aw}.circadian', empty)
    monkeypatch.setattr(f'{aw}.loops', empty_list)
    monkeypatch.setattr(f'{aw}.fragmentation', empty)
    monkeypatch.setattr(f'{aw}.attention', empty)
    monkeypatch.setattr(f'{aw}.focus_timeline', empty)
    monkeypatch.setattr(f'{web}.daily_browsing', empty)
    monkeypatch.setattr(f'{terminal}.shell_sessions', empty)
    monkeypatch.setattr('lynchpin.graph.terminal_patterns.detect_patterns', lambda **kwargs: ())
    monkeypatch.setattr(f'{web}.iter_streams', empty_list)
    monkeypatch.setattr(f'{system}.add_temporal_signals', lambda nodes, **kwargs: None)
    monkeypatch.setattr(f'{system}.add_readiness', lambda nodes, **kwargs: None)
    monkeypatch.setattr(f'{system}.add_health', lambda nodes, **kwargs: None)
    monkeypatch.setattr('lynchpin.materialization.materialized_window_overlaps', lambda *args, **kwargs: False)
    monkeypatch.setattr('lynchpin.graph.evidence_analysis.latest_artifacts', lambda **kwargs: ())
    empty_analysis_root = tmp_path / '_empty_analysis'
    empty_analysis_root.mkdir()
    monkeypatch.setattr('lynchpin.core.io.get_config', lambda: type('obj', (), {'analysis_output_dir': empty_analysis_root})())
    monkeypatch.setattr('lynchpin.graph.evidence_graph.source_readiness', lambda **kwargs: type('obj', (), {'caveats': ()})())
