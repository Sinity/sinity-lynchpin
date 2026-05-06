from datetime import date

from lynchpin.composite.source_readiness import render_source_readiness, source_readiness
from lynchpin.sources.polylogue import PolylogueReadiness


def test_source_readiness_reports_polylogue_degradation(monkeypatch, tmp_path):
    raw_log = tmp_path / "logs.raw-log.md"
    raw_log.write_text("- **2026-05-06 00:00:00** test\n", encoding="utf-8")

    class Config:
        activitywatch_db = tmp_path / "aw.db"
        atuin_db = tmp_path / "atuin.db"
        sinnix_root = tmp_path / "sinnix"
        polylogue_db = tmp_path / "polylogue.db"
        raw_log_file = raw_log
        repo_root = tmp_path / "repo"
        analysis_output_dir = tmp_path / "analysis"
        webhistory_ndjson = None
        webhistory_dir = tmp_path / "web"
        sleep_jsonl = tmp_path / "sleep.jsonl"
        samsung_gdpr_cloud_dir = tmp_path / "health"
        spotify_root = tmp_path / "spotify"
        reddit_export_dir = tmp_path / "reddit"
        fbmessenger_gdpr_root = tmp_path / "messenger"
        fbmessenger_db = tmp_path / "messenger.sqlite"
        raindrop_csv = tmp_path / "raindrop.csv"
        exports_root = tmp_path / "exports"

        def available_sources(self):
            return {
                "activitywatch": False,
                "atuin": False,
                "git_baseline": False,
                "webhistory": False,
                "sleep": False,
                "codex": False,
                "reddit": False,
                "spotify": False,
                "polylogue": True,
                "fbmessenger": False,
                "asciinema": False,
                "keylog": False,
                "goodreads": False,
                "raindrop": False,
                "wykop": False,
                "dendron": False,
                "samsung_gdpr_cloud": False,
                "clipboard": False,
                "irc": False,
                "raw_log": True,
            }

    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="degraded",
        reason="base archive usable",
        conversation_count=10,
        message_count=None,
        conversation_stats_count=10,
        session_profile_count=0,
        day_summary_count=0,
        work_event_count=0,
        provider_event_count=None,
        derives_profiles_from_base_tables=True,
        derives_day_summaries_from_profiles=True,
    )

    monkeypatch.setattr("lynchpin.composite.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.composite.source_readiness.archive_readiness", lambda include_heavy_counts=False: readiness)

    report = source_readiness(start=date(2026, 5, 1), end=date(2026, 5, 6))
    by_source = report.by_source()

    assert by_source["polylogue"].status == "partial"
    assert any("work-event products are unavailable" in caveat for caveat in by_source["polylogue"].caveats)
    assert by_source["raw_log"].count == 1
    assert by_source["github"].cost == "network"
    rendered = render_source_readiness(report)
    assert "Source" in rendered
    assert "polylogue" in rendered


def test_source_readiness_reflects_network_mode(monkeypatch, tmp_path):
    class Config:
        activitywatch_db = tmp_path / "aw.db"
        atuin_db = tmp_path / "atuin.db"
        sinnix_root = tmp_path / "sinnix"
        raw_log_file = tmp_path / "logs.raw-log.md"
        repo_root = tmp_path / "repo"
        analysis_output_dir = tmp_path / "analysis"
        webhistory_ndjson = None
        webhistory_dir = tmp_path / "web"
        sleep_jsonl = tmp_path / "sleep.jsonl"
        samsung_gdpr_cloud_dir = tmp_path / "health"
        spotify_root = tmp_path / "spotify"
        reddit_export_dir = tmp_path / "reddit"
        fbmessenger_gdpr_root = tmp_path / "messenger"
        fbmessenger_db = tmp_path / "messenger.sqlite"
        raindrop_csv = tmp_path / "raindrop.csv"
        exports_root = tmp_path / "exports"

        def available_sources(self):
            return {
                "activitywatch": False,
                "atuin": False,
                "git_baseline": False,
                "webhistory": False,
                "sleep": False,
                "codex": False,
                "reddit": False,
                "spotify": False,
                "polylogue": True,
                "fbmessenger": False,
                "asciinema": False,
                "keylog": False,
                "goodreads": False,
                "raindrop": False,
                "wykop": False,
                "dendron": False,
                "samsung_gdpr_cloud": False,
                "clipboard": False,
                "irc": False,
                "raw_log": False,
            }

    calls = {}
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="ok",
        reason="ready",
        conversation_count=10,
        message_count=None,
        conversation_stats_count=10,
        session_profile_count=10,
        day_summary_count=10,
        work_event_count=10,
        provider_event_count=10,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )

    def fake_archive_readiness(*, include_heavy_counts=False):
        calls["include_heavy_counts"] = include_heavy_counts
        return readiness

    monkeypatch.setattr("lynchpin.composite.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.composite.source_readiness.archive_readiness", fake_archive_readiness)

    report = source_readiness(
        start=date(2026, 5, 1),
        end=date(2026, 5, 6),
        include_heavy_counts=True,
        include_github_frontier=True,
    )

    assert calls["include_heavy_counts"] is True
    assert report.by_source()["polylogue"].cost == "local-heavy"
    assert report.by_source()["github"].status == "available"
    assert report.by_source()["github"].caveats == ()


def test_source_readiness_reports_analysis_artifacts(monkeypatch, tmp_path):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "polylogue_metrics.json").write_text(
        '{"generated_at_utc":"2026-05-06T12:00:00+00:00","totals":{}}',
        encoding="utf-8",
    )

    class Config:
        activitywatch_db = tmp_path / "aw.db"
        atuin_db = tmp_path / "atuin.db"
        sinnix_root = tmp_path / "sinnix"
        raw_log_file = tmp_path / "logs.raw-log.md"
        repo_root = tmp_path / "repo"
        analysis_output_dir = analysis_dir
        webhistory_ndjson = None
        webhistory_dir = tmp_path / "web"
        sleep_jsonl = tmp_path / "sleep.jsonl"
        samsung_gdpr_cloud_dir = tmp_path / "health"
        spotify_root = tmp_path / "spotify"
        reddit_export_dir = tmp_path / "reddit"
        fbmessenger_gdpr_root = tmp_path / "messenger"
        fbmessenger_db = tmp_path / "messenger.sqlite"
        raindrop_csv = tmp_path / "raindrop.csv"
        exports_root = tmp_path / "exports"

        def available_sources(self):
            return {
                "activitywatch": False,
                "atuin": False,
                "git_baseline": False,
                "webhistory": False,
                "sleep": False,
                "codex": False,
                "reddit": False,
                "spotify": False,
                "polylogue": True,
                "fbmessenger": False,
                "asciinema": False,
                "keylog": False,
                "goodreads": False,
                "raindrop": False,
                "wykop": False,
                "dendron": False,
                "samsung_gdpr_cloud": False,
                "clipboard": False,
                "irc": False,
                "raw_log": False,
            }

    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="ok",
        reason="ready",
        conversation_count=10,
        message_count=None,
        conversation_stats_count=10,
        session_profile_count=10,
        day_summary_count=10,
        work_event_count=10,
        provider_event_count=10,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )

    monkeypatch.setattr("lynchpin.composite.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.composite.source_readiness.archive_readiness", lambda include_heavy_counts=False: readiness)

    report = source_readiness(start=date(2026, 5, 1), end=date(2026, 5, 6))
    analysis = report.by_source()["analysis"]

    assert analysis.status == "available"
    assert analysis.count == 1
    assert analysis.path == str(analysis_dir)
