import pytest


class _ReadyMaterialization:
    def to_json(self):
        return {"status": "ready", "reason": "test fixture"}


@pytest.fixture(autouse=True)
def _no_analysis_claims(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.analysis_claims", lambda **kwargs: ()
    )


@pytest.fixture(autouse=True)
def _no_substrate_overlap(monkeypatch):
    """Skip SQL overlap path in unit tests — tested by test_substrate_views.py."""
    monkeypatch.setattr(
        "lynchpin.graph.evidence_edges.overlap_edges_via_substrate",
        lambda nodes, **kwargs: (),
    )


@pytest.fixture(autouse=True)
def _mock_empty_sources(monkeypatch, tmp_path):
    """Default all source functions to return empty — individual tests
    override with their own monkeypatch.setattr calls as needed.

    Evidence graph construction calls source-family modules directly, so tests
    patch those owners instead of raw source modules. Individual tests override
    these defaults for the sources they need.
    """
    aw = "lynchpin.graph.evidence_activitywatch"
    git = "lynchpin.graph.evidence_git"
    polylogue = "lynchpin.graph.evidence_polylogue"
    raw_log = "lynchpin.graph.evidence_raw_log"
    system = "lynchpin.graph.evidence_system_signals"
    terminal = "lynchpin.graph.evidence_terminal"
    web = "lynchpin.graph.evidence_web_media"

    def empty(*args, **kwargs):
        return ()

    def empty_list(*args, **kwargs):
        return []

    monkeypatch.setattr(f"{git}.commit_facts", empty)
    monkeypatch.setattr(f"{polylogue}.session_profiles_for_date", empty)
    monkeypatch.setattr(f"{polylogue}.work_events", empty)
    monkeypatch.setattr(f"{raw_log}.entries_in_range", empty)
    monkeypatch.setattr(f"{aw}.project_focus_days", empty_list)
    monkeypatch.setattr(f"{aw}.deep_work", empty_list)
    monkeypatch.setattr(f"{aw}.circadian", empty)
    monkeypatch.setattr(f"{aw}.loops", empty_list)
    monkeypatch.setattr(f"{aw}.fragmentation", empty)
    monkeypatch.setattr(f"{aw}.attention", empty)
    monkeypatch.setattr(f"{aw}.focus_spans", empty)
    monkeypatch.setattr(f"{aw}.focus_timeline", empty)
    monkeypatch.setattr(f"{aw}.ensure_activitywatch_derived", lambda **kwargs: None)
    monkeypatch.setattr(f"{web}.daily_browsing", empty)
    monkeypatch.setattr(f"{terminal}.shell_sessions", empty)
    monkeypatch.setattr(
        "lynchpin.graph.terminal_patterns.detect_patterns", lambda **kwargs: ()
    )
    monkeypatch.setattr(f"{system}.add_temporal_signals", lambda nodes, **kwargs: None)
    monkeypatch.setattr(f"{system}.add_readiness", lambda nodes, **kwargs: None)
    monkeypatch.setattr(f"{system}.add_health", lambda nodes, **kwargs: None)
    # Skip personal-product emitters (activity_content_day, irc, arbtt, etc.) —
    # tests that exercise them mock materialized_window_overlaps explicitly.
    monkeypatch.setattr(
        "lynchpin.materialization.materialized_window_overlaps",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *args, **kwargs: _ReadyMaterialization(),
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_spotify_daily_signals",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_title_usage",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.google_takeout_products.iter_daily_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.bookmarks.daily_bookmark_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.communications.daily_communication_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.irc_raw.daily_irc_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.arbtt.daily_arbtt_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    empty_analysis_root = tmp_path / "_empty_analysis"
    empty_analysis_root.mkdir()
    monkeypatch.setattr(
        "lynchpin.core.io.get_config",
        lambda: type("obj", (), {"analysis_output_dir": empty_analysis_root})(),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: type("obj", (), {"caveats": ()})(),
    )
