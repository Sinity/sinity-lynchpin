"""Tests for the lynchpin Polylogue adapter contract."""

from datetime import date

from lynchpin.sources import polylogue
from lynchpin.sources.polylogue import DaySessionSummary


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
