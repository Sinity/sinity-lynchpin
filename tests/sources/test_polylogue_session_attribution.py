"""Coverage for the index-DB session/repo overlap attributor (fallback tier)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from lynchpin.sources.polylogue_session_attribution import (
    SessionRepoInterval,
    attribute_spans_by_session_overlap,
    session_repo_intervals,
)


@dataclass(frozen=True)
class _Span:
    start: datetime
    end: datetime


def _dt(hour: int, minute: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 4, day, hour, minute, tzinfo=timezone.utc)


def _interval(session_id: str, project: str, start: datetime, end: datetime) -> SessionRepoInterval:
    return SessionRepoInterval(session_id=session_id, project=project, start=start, end=end)


def test_attributes_span_fully_inside_one_session():
    span = _Span(_dt(10, 5), _dt(10, 25))
    intervals = [_interval("s1", "sinnix", _dt(10, 0), _dt(11, 0))]

    [attr] = attribute_spans_by_session_overlap([span], intervals)

    assert attr is not None
    assert attr.project == "sinnix"
    assert attr.confidence == pytest.approx(1.0)


def test_dominant_overlap_wins_among_concurrent_sessions():
    span = _Span(_dt(10, 0), _dt(10, 30))
    intervals = [
        # Only overlaps the first 5 minutes of the span.
        _interval("s1", "sinex", _dt(9, 55), _dt(10, 5)),
        # Overlaps the full span.
        _interval("s2", "sinity-lynchpin", _dt(9, 0), _dt(11, 0)),
    ]

    [attr] = attribute_spans_by_session_overlap([span], intervals)

    assert attr is not None
    assert attr.project == "sinity-lynchpin"
    assert attr.session_id == "s2"


def test_below_confidence_floor_returns_none():
    # Span mostly outside the session; only the 30s slack window ties them.
    span = _Span(_dt(10, 0), _dt(10, 30))
    intervals = [_interval("s1", "sinnix", _dt(10, 30), _dt(11, 0))]

    [attr] = attribute_spans_by_session_overlap([span], intervals, slack_s=30.0)

    assert attr is None


def test_no_overlap_returns_none():
    span = _Span(_dt(10, 0), _dt(10, 30))
    intervals = [_interval("s1", "sinnix", _dt(14, 0), _dt(15, 0))]

    [attr] = attribute_spans_by_session_overlap([span], intervals)

    assert attr is None


def test_day_bucketing_scopes_candidates_to_touched_days():
    # A session on day 22 must not attribute a span on day 21.
    span = _Span(_dt(10, 0, day=21), _dt(10, 30, day=21))
    intervals = [_interval("s1", "sinnix", _dt(10, 0, day=22), _dt(11, 0, day=22))]

    [attr] = attribute_spans_by_session_overlap([span], intervals)

    assert attr is None


def _make_index_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            created_at_ms INTEGER,
            updated_at_ms INTEGER
        );
        CREATE TABLE session_repos (
            session_id TEXT,
            root_path TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        ("claude-code-session:abc", 1_776_000_000_000, 1_776_003_600_000),
    )
    conn.execute(
        "INSERT INTO session_repos VALUES (?, ?)",
        ("claude-code-session:abc", "/realm/project/sinity-lynchpin"),
    )
    # A repo checkout outside /realm/project must be filtered out.
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        ("claude-code-session:def", 1_776_000_000_000, 1_776_003_600_000),
    )
    conn.execute(
        "INSERT INTO session_repos VALUES (?, ?)",
        ("claude-code-session:def", "/home/sinity/scratch"),
    )
    # created > updated (data corruption) must be filtered out too.
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        ("claude-code-session:ghi", 1_776_003_600_000, 1_776_000_000_000),
    )
    conn.execute(
        "INSERT INTO session_repos VALUES (?, ?)",
        ("claude-code-session:ghi", "/realm/project/sinnix"),
    )
    conn.commit()
    conn.close()


def test_session_repo_intervals_reads_real_schema_shape(tmp_path):
    db_path = str(tmp_path / "index.db")
    _make_index_db(db_path)
    session_repo_intervals.cache_clear()

    intervals = session_repo_intervals(db_path)

    assert intervals == (
        SessionRepoInterval(
            session_id="claude-code-session:abc",
            project="sinity-lynchpin",
            start=datetime.fromtimestamp(1_776_000_000_000 / 1000, tz=timezone.utc),
            end=datetime.fromtimestamp(1_776_003_600_000 / 1000, tz=timezone.utc),
        ),
    )
    session_repo_intervals.cache_clear()
