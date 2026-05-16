"""Tests for per-commit AI-assistance density (Arc L.2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.analysis.active.ai_assist_density import build_active_ai_assist_density
from lynchpin.sources.polylogue import WorkEvent

UTC = timezone.utc


def _event(
    *,
    event_id: str,
    start: datetime,
    duration_min: float,
    file_paths: tuple[str, ...] = (),
    kind: str = "implementation",
    confidence: float = 0.85,
    provider: str = "claude-code",
    conversation_id: str = "c1",
) -> WorkEvent:
    return WorkEvent(
        event_id=event_id,
        conversation_id=conversation_id,
        provider=provider,
        kind=kind,
        confidence=confidence,
        start=start,
        end=start + timedelta(minutes=duration_min),
        duration_ms=int(duration_min * 60_000),
        file_paths=file_paths,
        tools_used=("Edit",),
        summary="t",
    )


def _commit_payload(
    *,
    sha: str,
    project: str,
    timestamp: datetime,
    paths: tuple[str, ...],
    subject: str = "feat: x",
) -> dict:
    return {
        "commits": [
            {
                "project": project,
                "sha": sha,
                "subject": subject,
                "timestamp": timestamp.isoformat(),
                "date": timestamp.date().isoformat(),
                "paths": list(paths),
            }
        ]
    }


def test_high_density_three_long_overlapping_events(monkeypatch, tmp_path):
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    commit_ts = base + timedelta(minutes=30)  # inside all 3 event windows below
    commit = _commit_payload(
        sha="abc", project="polylogue",
        timestamp=commit_ts, paths=("/realm/project/polylogue/src/foo.py",),
    )
    # Each event is 50 min, staggered by 10 min — all three windows include
    # commit_ts at base+30min. Total event time = 150 min ≥ 2h threshold.
    events = (
        _event(event_id=f"we{i}", start=base + timedelta(minutes=i*10),
               duration_min=50, file_paths=("/realm/project/polylogue/src/foo.py",))
        for i in range(3)
    )
    payload = build_active_ai_assist_density(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        commit_payload=commit,
        work_events_iter=tuple(events),
    )
    assert payload["summary"]["high"] == 1
    assert payload["commits"][0]["ai_assist_density"] == "high"
    assert payload["commits"][0]["supporting_event_count"] == 3
    assert payload["commits"][0]["supporting_total_duration_s"] >= 2 * 3600


def test_medium_density_one_overlapping_event(tmp_path):
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    commit_ts = base + timedelta(minutes=20)
    commit = _commit_payload(
        sha="abc", project="polylogue",
        timestamp=commit_ts, paths=("/realm/project/polylogue/src/foo.py",),
    )
    events = (
        _event(event_id="we1", start=base, duration_min=45,
               file_paths=("/realm/project/polylogue/src/foo.py",)),
    )
    payload = build_active_ai_assist_density(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        commit_payload=commit,
        work_events_iter=events,
    )
    assert payload["commits"][0]["ai_assist_density"] == "medium"
    assert payload["commits"][0]["supporting_event_count"] == 1


def test_same_day_session_without_file_overlap_is_none(tmp_path):
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    commit_ts = base + timedelta(hours=8)
    commit = _commit_payload(
        sha="abc", project="polylogue",
        timestamp=commit_ts,
        paths=("/realm/project/polylogue/src/bar.py",),
    )
    events = (
        # Same-day session with different file (still attributes to project via the path).
        _event(event_id="we1", start=base, duration_min=20,
               file_paths=("/realm/project/polylogue/src/foo.py",)),
    )
    payload = build_active_ai_assist_density(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        commit_payload=commit,
        work_events_iter=events,
    )
    assert payload["commits"][0]["ai_assist_density"] == "none"
    assert payload["commits"][0]["supporting_event_count"] == 0
    assert payload["commits"][0]["caveats"] == []


def test_none_density_no_ai_signal(tmp_path):
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    commit = _commit_payload(
        sha="abc", project="polylogue", timestamp=base,
        paths=("/realm/project/polylogue/src/foo.py",),
    )
    payload = build_active_ai_assist_density(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        commit_payload=commit,
        work_events_iter=(),
    )
    assert payload["commits"][0]["ai_assist_density"] == "none"
    assert payload["summary"]["none"] == 1


def test_low_tier_caveat_when_majority_low_confidence(tmp_path):
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    commit_ts = base + timedelta(minutes=10)  # inside both 15-min event windows
    commit = _commit_payload(
        sha="abc", project="polylogue", timestamp=commit_ts,
        paths=("/realm/project/polylogue/src/foo.py",),
    )
    events = (
        _event(event_id="we1", start=base, duration_min=15,
               file_paths=("/realm/project/polylogue/src/foo.py",), confidence=0.3),
        _event(event_id="we2", start=base, duration_min=15,
               file_paths=("/realm/project/polylogue/src/foo.py",), confidence=0.4),
    )
    payload = build_active_ai_assist_density(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        commit_payload=commit,
        work_events_iter=events,
    )
    row = payload["commits"][0]
    assert row["low_tier_event_count"] == 2
    assert any("low Polylogue kind confidence" in c for c in row["caveats"])
