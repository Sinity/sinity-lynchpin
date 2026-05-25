"""Tests for the Polylogue inference quality dashboard (M.18 + M.19)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.analysis.ecosystem.polylogue_inference_quality import (
    build_polylogue_inference_quality,
)
from lynchpin.sources.polylogue import WorkEvent

UTC = timezone.utc


def _event(
    *,
    event_id: str,
    kind: str,
    confidence: float,
    file_paths: tuple[str, ...] = (),
    tools_used: tuple[str, ...] = (),
    duration_ms: int = 0,
    start: datetime,
) -> WorkEvent:
    return WorkEvent(
        event_id=event_id,
        conversation_id="c1",
        provider="claude-code",
        kind=kind,
        confidence=confidence,
        start=start,
        end=start + timedelta(minutes=15),
        duration_ms=duration_ms,
        file_paths=file_paths,
        tools_used=tools_used,
        summary="t",
    )


def test_agreement_dominates_when_features_align_with_source_kind():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    # Source says implementation, file_paths support implementation,
    # Edit+Write tools, 30min duration → agreement.
    events = [
        _event(
            event_id=f"we{i}",
            kind="implementation",
            confidence=0.9,
            file_paths=("lynchpin/foo.py",),
            tools_used=("Edit", "Write"),
            duration_ms=30 * 60_000,
            start=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    payload = build_polylogue_inference_quality(
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        events_iter=events,
    )
    assert payload["summary"]["agreement_count"] == 5
    assert payload["summary"]["agreement_rate"] == 1.0


def test_disagreement_when_features_contradict_source_kind():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    # Source says implementation, but file_paths are all tests/ + Bash-heavy
    events = [
        _event(
            event_id=f"we{i}",
            kind="implementation",
            confidence=0.55,
            file_paths=("tests/test_foo.py", "tests/test_bar.py"),
            tools_used=("Bash", "Bash", "Bash"),
            duration_ms=15 * 60_000,
            start=base + timedelta(hours=i),
        )
        for i in range(3)
    ]
    payload = build_polylogue_inference_quality(
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        events_iter=events,
    )
    summary = payload["summary"]
    assert summary["disagreement_count"] == 3
    assert summary["agreement_count"] == 0
    # Confusion matrix has the implementation→testing/debugging pair
    pairs = payload["top_disagreement_pairs"]
    assert pairs
    assert pairs[0]["source_kind"] == "implementation"


def test_per_kind_row_counts_match_input():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    events = [
        _event(
            event_id="we1",
            kind="research",
            confidence=0.7,
            file_paths=("docs/notes.md",),
            tools_used=("Read",),
            duration_ms=10 * 60_000,
            start=base,
        ),
        _event(
            event_id="we2",
            kind="implementation",
            confidence=0.9,
            file_paths=("lynchpin/foo.py",),
            tools_used=("Edit", "Write"),
            duration_ms=30 * 60_000,
            start=base + timedelta(hours=1),
        ),
    ]
    payload = build_polylogue_inference_quality(
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        events_iter=events,
    )
    by_kind = {row["kind"]: row for row in payload["per_kind"]}
    assert by_kind["research"]["source_count"] == 1
    assert by_kind["implementation"]["source_count"] == 1


def test_weekly_disagreement_rate_per_iso_week():
    week1_monday = datetime(2026, 4, 27, 12, tzinfo=UTC)  # Mon Apr 27
    week2_monday = datetime(2026, 5, 4, 12, tzinfo=UTC)  # Mon May 4
    events = [
        # Week 1: 2 agreements
        _event(
            event_id="w1a",
            kind="implementation",
            confidence=0.9,
            file_paths=("lynchpin/foo.py",),
            tools_used=("Edit", "Write"),
            duration_ms=30 * 60_000,
            start=week1_monday,
        ),
        _event(
            event_id="w1b",
            kind="implementation",
            confidence=0.9,
            file_paths=("lynchpin/bar.py",),
            tools_used=("Edit", "Write"),
            duration_ms=30 * 60_000,
            start=week1_monday + timedelta(hours=1),
        ),
        # Week 2: 1 disagreement
        _event(
            event_id="w2a",
            kind="implementation",
            confidence=0.55,
            file_paths=("tests/test_x.py",),
            tools_used=("Bash", "Bash", "Bash"),
            duration_ms=15 * 60_000,
            start=week2_monday,
        ),
    ]
    payload = build_polylogue_inference_quality(
        start=date(2026, 4, 27),
        end=date(2026, 5, 5),
        events_iter=events,
    )
    by_week = {row["week_start"]: row for row in payload["weekly"]}
    assert by_week["2026-04-27"]["disagreement_rate"] == 0.0
    assert by_week["2026-05-04"]["disagreement_rate"] == 1.0


def test_low_source_confidence_count_per_kind():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    events = [
        _event(
            event_id="lc1",
            kind="planning",
            confidence=0.3,
            file_paths=("docs/x.md",),
            tools_used=(),
            duration_ms=5 * 60_000,
            start=base,
        ),
        _event(
            event_id="lc2",
            kind="planning",
            confidence=0.4,
            file_paths=("docs/y.md",),
            tools_used=(),
            duration_ms=5 * 60_000,
            start=base + timedelta(hours=1),
        ),
        _event(
            event_id="hc1",
            kind="planning",
            confidence=0.85,
            file_paths=("docs/z.md",),
            tools_used=(),
            duration_ms=5 * 60_000,
            start=base + timedelta(hours=2),
        ),
    ]
    payload = build_polylogue_inference_quality(
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        events_iter=events,
    )
    by_kind = {row["kind"]: row for row in payload["per_kind"]}
    assert by_kind["planning"]["low_source_confidence_count"] == 2


def test_empty_window_yields_zero_summary():
    payload = build_polylogue_inference_quality(
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        events_iter=(),
    )
    assert payload["summary"]["total_events"] == 0
    assert payload["summary"]["agreement_rate"] == 0.0
    assert payload["per_kind"] == []
    assert payload["weekly"] == []
