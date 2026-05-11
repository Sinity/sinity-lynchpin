"""Tests for AI-attribution longitudinal backfill (M.13)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.analysis.ecosystem.ai_attribution_history import (
    build_active_ai_attribution_history,
)
from lynchpin.sources.polylogue import SessionProfile

UTC = timezone.utc


def _profile(*, conv_id: str, day: date, kind: str = "implementation",
            duration_min: int = 30) -> SessionProfile:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=12)
    end = start + timedelta(minutes=duration_min)
    return SessionProfile(
        conversation_id=conv_id,
        provider="claude-code",
        title="t",
        message_count=10,
        word_count=100,
        first_message_at=start,
        last_message_at=end,
        engaged_duration_ms=duration_min * 60_000,
        wall_duration_ms=duration_min * 60_000,
        work_event_kind=kind,
        work_event_projects=("polylogue",),
        total_cost_usd=0.0,
        canonical_session_date=day,
        tool_use_count=0,
        thinking_count=0,
        auto_tags=(),
    )


def _commit(*, sha: str, project: str, day: date, hour: int = 12) -> dict:
    ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=hour).isoformat()
    return {
        "project": project,
        "sha": sha,
        "subject": "feat: x",
        "timestamp": ts,
        "date": day.isoformat(),
        "paths": ["src/foo.py"],
    }


def test_monthly_aggregation_groups_commits_by_year_month():
    payload = {"commits": [
        _commit(sha="m1c1", project="demo", day=date(2026, 5, 5)),
        _commit(sha="m1c2", project="demo", day=date(2026, 5, 15)),
        _commit(sha="m2c1", project="demo", day=date(2026, 6, 1)),
    ]}
    out = build_active_ai_attribution_history(
        commit_payload=payload,
        session_profiles=(),
    )
    by_month = {row["month"]: row for row in out["monthly"]}
    assert by_month["2026-05"]["total_commits"] == 2
    assert by_month["2026-06"]["total_commits"] == 1
    assert by_month["2026-05"]["attributed_none"] == 2
    assert by_month["2026-06"]["ai_ratio"] == 0.0


def test_high_attribution_when_session_window_overlaps_commit():
    commit_day = date(2026, 5, 7)
    payload = {"commits": [_commit(sha="abc", project="polylogue", day=commit_day, hour=12)]}
    out = build_active_ai_attribution_history(
        commit_payload=payload,
        session_profiles=[_profile(conv_id="c1", day=commit_day, duration_min=120)],
    )
    by_month = {row["month"]: row for row in out["monthly"]}
    row = by_month["2026-05"]
    assert row["attributed_high"] == 1
    assert row["attributed_none"] == 0
    assert row["ai_ratio"] == 1.0
    assert "implementation" in row["dominant_kinds"]


def test_project_totals_aggregate_across_months():
    payload = {"commits": [
        _commit(sha="a", project="demo", day=date(2026, 4, 5)),
        _commit(sha="b", project="demo", day=date(2026, 5, 5)),
        _commit(sha="c", project="demo", day=date(2026, 6, 5)),
    ]}
    out = build_active_ai_attribution_history(
        commit_payload=payload,
        session_profiles=(),
    )
    project = next(row for row in out["project_totals"] if row["project"] == "demo")
    assert project["total_commits"] == 3
    assert project["none"] == 3
    assert project["ai_ratio"] == 0.0


def test_window_reflects_actual_commit_date_span():
    payload = {"commits": [
        _commit(sha="early", project="demo", day=date(2024, 3, 15)),
        _commit(sha="late", project="demo", day=date(2026, 5, 7)),
    ]}
    out = build_active_ai_attribution_history(
        commit_payload=payload,
        session_profiles=(),
    )
    assert out["window"]["start"] == "2024-03"
    assert out["window"]["end"] == "2026-05"


def test_project_filter_isolates_selected():
    payload = {"commits": [
        _commit(sha="a", project="alpha", day=date(2026, 5, 5)),
        _commit(sha="b", project="beta", day=date(2026, 5, 5)),
    ]}
    out = build_active_ai_attribution_history(
        commit_payload=payload,
        session_profiles=(),
        projects=["alpha"],
    )
    assert {row["project"] for row in out["monthly"]} == {"alpha"}
    assert {row["project"] for row in out["project_totals"]} == {"alpha"}


def test_empty_input_yields_empty_payload():
    out = build_active_ai_attribution_history(
        commit_payload={"commits": []},
        session_profiles=(),
    )
    assert out["monthly"] == []
    assert out["project_totals"] == []
    assert out["window"]["start"] == ""
