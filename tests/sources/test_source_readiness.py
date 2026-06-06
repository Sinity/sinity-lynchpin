from datetime import date, datetime
import sqlite3

import pytest

from lynchpin.graph.source_readiness import render_source_readiness, source_readiness
from lynchpin.graph.coverage import CoverageReport, SourceCoverage
from lynchpin.materialization import MaterializationResult
from lynchpin.sources.source_observations import SourceObservation
from lynchpin.sources.polylogue import PolylogueReadiness


@pytest.fixture(autouse=True)
def _empty_observation_contract(monkeypatch):
    monkeypatch.setattr("lynchpin.graph.source_readiness.source_observations", lambda: ())
    monkeypatch.setattr("lynchpin.sources.xtask_history.xtask_history_paths", lambda: ())
    monkeypatch.setattr(
        "lynchpin.graph.source_readiness.coverage_report",
        lambda *, start, end, repair_materializations=True: CoverageReport(
            start=start,
            end=end,
            generated_at=datetime(2026, 5, 1),
            sources=(),
        ),
    )


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
        reason="session-profile products are stale",
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

    monkeypatch.setattr("lynchpin.graph.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.graph.source_readiness.archive_readiness", lambda include_polylogue_product_counts=False: readiness)
    coverage_calls = []

    def fake_coverage_report(*, start, end, repair_materializations=True):
        coverage_calls.append((start, end, repair_materializations))
        return CoverageReport(start=start, end=end, generated_at=datetime(2026, 5, 1), sources=())

    monkeypatch.setattr("lynchpin.graph.source_readiness.coverage_report", fake_coverage_report)
    ensure_calls = []

    def fake_ensure_materialized(name, *, window=None, budget="inline", cfg=None, force=False):
        ensure_calls.append((name, window, budget))
        return MaterializationResult(
            name=name,
            status="blocked",
            changed=False,
            reason="missing",
            elapsed_ms=0,
            product_paths=(),
            source_high_water={"row_count": 0, "first_date": None, "last_date": None},
            coverage={"relation": "unavailable"},
        )

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    report = source_readiness(start=date(2026, 5, 1), end=date(2026, 5, 6))
    by_source = report.by_source()

    assert report.end == date(2026, 5, 6)
    assert coverage_calls == [(date(2026, 5, 1), date(2026, 5, 7), True)]
    assert by_source["polylogue"].status == "partial"
    assert any("session-profile products" in caveat for caveat in by_source["polylogue"].caveats)
    assert any("work-event products are unavailable" in caveat for caveat in by_source["polylogue"].caveats)
    assert by_source["raw_log"].count == 1
    assert by_source["github"].cost == "materialized"
    assert by_source["github"].reason == "missing"
    assert by_source["github"].caveats == (
        "missing",
        "frontier rows are available in github_context but not rendered by this read",
    )
    assert ensure_calls == [
        ("title_metadata", (date(2026, 5, 1), date(2026, 5, 7)), "inline"),
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 7)), "inline"),
        ("github_context", (date(2026, 5, 1), date(2026, 5, 7)), "inline"),
    ]
    rendered = render_source_readiness(report)
    assert "Source" in rendered
    assert "polylogue" in rendered

    monkeypatch.setattr(
        "lynchpin.graph.source_readiness.source_observations",
        lambda: (_ for _ in ()).throw(
            AssertionError("audit-only readiness should not scan source observations")
        ),
    )
    ensure_calls.clear()
    source_readiness(
        start=date(2026, 5, 1),
        end=date(2026, 5, 6),
        repair_materializations=False,
    )
    assert coverage_calls[-1] == (date(2026, 5, 1), date(2026, 5, 7), False)
    assert ensure_calls == [
        ("title_metadata", (date(2026, 5, 1), date(2026, 5, 7)), "manual"),
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 7)), "manual"),
        ("github_context", (date(2026, 5, 1), date(2026, 5, 7)), "manual"),
    ]


def test_xtask_source_readiness_summarizes_ledgers(monkeypatch, tmp_path):
    db = tmp_path / "xtask-history.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE invocations (started_at TEXT)")
        conn.execute("CREATE TABLE stage_timings (id INTEGER)")
        conn.execute("CREATE TABLE test_results (id INTEGER)")
        conn.execute("INSERT INTO invocations VALUES ('2026-05-30T10:00:00+00:00')")
        conn.execute("INSERT INTO invocations VALUES ('2026-05-31T10:00:00+00:00')")
        conn.execute("INSERT INTO stage_timings VALUES (1)")
        conn.execute("INSERT INTO test_results VALUES (1)")
        conn.execute("INSERT INTO test_results VALUES (2)")

    monkeypatch.setattr("lynchpin.sources.xtask_history.xtask_history_paths", lambda: (("live", db),))

    from lynchpin.graph.source_readiness import _xtask_history_source

    readiness = _xtask_history_source()

    assert readiness.source == "xtask_history"
    assert readiness.status == "available"
    assert readiness.count == 2
    assert readiness.first_date == date(2026, 5, 30)
    assert readiness.last_date == date(2026, 5, 31)
    assert "2 stages" not in readiness.reason
    assert "1 stages" in readiness.reason
    assert "2 test results" in readiness.reason


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

    def fake_archive_readiness(*, include_polylogue_product_counts=False):
        calls["include_polylogue_product_counts"] = include_polylogue_product_counts
        return readiness

    monkeypatch.setattr("lynchpin.graph.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.graph.source_readiness.archive_readiness", fake_archive_readiness)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget="inline", cfg=None, force=False: MaterializationResult(
            name=name,
            status="ready",
            changed=False,
            reason="GitHub lifecycle context product is materialized within the 48h network refresh contract",
            elapsed_ms=0,
            product_paths=(tmp_path / "github/context.ndjson",),
            source_high_water={"row_count": 1, "first_date": "2026-05-01", "last_date": "2026-05-06"},
            coverage={"relation": "covered"},
        ),
    )

    report = source_readiness(
        start=date(2026, 5, 1),
        end=date(2026, 5, 6),
        include_polylogue_product_counts=True,
        include_github_frontier=True,
    )

    assert calls["include_polylogue_product_counts"] is True
    assert report.by_source()["polylogue"].cost == "materialized"
    assert report.by_source()["github"].status == "available"
    assert report.by_source()["github"].caveats == ()
    assert "frontier rows are enabled" in report.by_source()["github"].reason


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

    monkeypatch.setattr("lynchpin.graph.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.graph.source_readiness.archive_readiness", lambda include_polylogue_product_counts=False: readiness)

    report = source_readiness(start=date(2026, 5, 1), end=date(2026, 5, 6))
    analysis = report.by_source()["analysis"]

    assert analysis.status == "available"
    assert analysis.count == 1
    assert analysis.path == str(analysis_dir)


def test_source_readiness_uses_observed_source_observation_not_directory_mtime(
    monkeypatch, tmp_path
):
    spotify_dir = tmp_path / "spotify"
    spotify_dir.mkdir()

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
        spotify_root = spotify_dir
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
                "spotify": True,
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
        status="ready",
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

    monkeypatch.setattr("lynchpin.graph.source_readiness.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.graph.source_readiness.archive_readiness", lambda include_polylogue_product_counts=False: readiness)
    monkeypatch.setattr(
        "lynchpin.graph.source_readiness.source_observations",
        lambda: (
            SourceObservation(
                source="spotify",
                available=True,
                last_observed=date(2025, 12, 18),
                basis="substrate",
                recommendation=None,
                path=None,
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.source_readiness.coverage_report",
        lambda *, start, end, repair_materializations=True: CoverageReport(
            start=start,
            end=end,
            generated_at=datetime(2026, 5, 6),
            sources=(
                SourceCoverage(
                    source="spotify",
                    status="out_of_range",
                    reason="parsed rows do not intersect the requested window",
                    requested_start=start,
                    requested_end=end,
                    first_date=date(2013, 2, 12),
                    last_date=date(2025, 12, 18),
                    row_count=10,
                ),
            ),
        ),
    )

    report = source_readiness(start=date(2026, 5, 1), end=date(2026, 5, 6))
    spotify = report.by_source()["spotify"]

    assert spotify.status == "out_of_range"
    assert spotify.last_date == date(2025, 12, 18)
    assert spotify.caveats[0] == "parsed rows do not intersect the requested window"
