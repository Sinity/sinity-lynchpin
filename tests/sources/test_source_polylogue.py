"""Tests for the lynchpin Polylogue adapter contract."""

from datetime import date
from types import SimpleNamespace

from lynchpin.sources import polylogue
from lynchpin.sources.polylogue import DaySessionSummary


def _readiness_entry(name: str, rows: int, verdict: str = "ready", expected: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        insight_name=name,
        row_count=rows,
        verdict=verdict,
        expected_row_count=expected,
    )


def _readiness_report(*entries: SimpleNamespace, total_conversations: int = 0) -> SimpleNamespace:
    return SimpleNamespace(insights=entries, total_conversations=total_conversations)


def test_daily_activity_uses_day_summaries_without_profile_query(monkeypatch):
    def fake_summaries(*, start=None, end=None):
        return [
            DaySessionSummary(
                date=date(2026, 4, 22),
                session_count=3,
                total_cost_usd=0.0,
                total_messages=30,
                total_words=300,
                work_event_breakdown={"implementation": 2, "review": 1},
                repos_active=("sinity-lynchpin", "polylogue"),
                providers={"claude-code": 3},
            )
        ]

    def fail_profiles():
        raise AssertionError("daily_activity should prefer day summaries")

    monkeypatch.setattr(polylogue, "day_session_summaries", fake_summaries)
    monkeypatch.setattr(polylogue, "iter_session_profiles", fail_profiles)

    result = polylogue.daily_activity(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].provider == "claude-code"
    assert result[0].session_count == 3
    assert result[0].dominant_work_kind == "implementation"
    assert result[0].projects == ("sinity-lynchpin", "polylogue")


def test_work_pattern_uses_repos_active_from_day_summaries(monkeypatch):
    monkeypatch.setattr(polylogue, "day_session_summaries", lambda *, start=None, end=None: [
        DaySessionSummary(
            date=date(2026, 4, 22),
            session_count=1,
            total_cost_usd=0.0,
            total_messages=10,
            total_words=100,
            work_event_breakdown={"debugging": 2},
            repos_active=("sinity-lynchpin",),
            providers={"codex": 1},
        )
    ])

    result = polylogue.work_pattern(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].work_kind == "debugging"
    assert result[0].session_count == 2
    assert result[0].top_projects == ("sinity-lynchpin",)


def test_archive_readiness_reports_product_degradation(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=lambda query: _readiness_report(
                _readiness_entry("session_profiles", 0, "empty", 1),
                _readiness_entry("day_session_summaries", 0, "empty", 1),
                _readiness_entry("session_work_events", 0, "empty", 1),
                total_conversations=1,
            )
        ),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "degraded"
    assert readiness.conversation_count == 1
    assert readiness.message_count is None
    assert readiness.conversation_stats_count == 1
    assert readiness.session_profile_count == 0
    assert readiness.derives_profiles_from_base_tables is False
    assert readiness.derives_day_summaries_from_profiles is False
    assert "session_profiles" in readiness.reason


def test_archive_readiness_reports_facade_failure(monkeypatch, tmp_path):
    db = tmp_path / "missing.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)

    def fail(query):
        raise RuntimeError("schema mismatch")

    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(insight_readiness_report=fail),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "unavailable"
    assert "schema mismatch" in readiness.reason


def test_archive_readiness_reports_populated_products(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=lambda query: _readiness_report(
                _readiness_entry("session_profiles", 3, "ready", 3),
                _readiness_entry("day_session_summaries", 2, "ready", 2),
                _readiness_entry("session_work_events", 7, "ready", 7),
                total_conversations=3,
            )
        ),
    )

    readiness = polylogue.archive_readiness(include_heavy_counts=True)

    assert readiness.status == "ready"
    assert readiness.conversation_count == 3
    assert readiness.session_profile_count == 3
    assert readiness.day_summary_count == 2
    assert readiness.work_event_count == 7
    assert readiness.message_count is None
    assert readiness.provider_event_count is None
