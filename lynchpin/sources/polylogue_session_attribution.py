"""Session/repo time-overlap project attribution via the raw Polylogue index DB.

Fallback tier for ``activitywatch._enrich_with_polylogue``: the primary tier
attributes spans via polylogue ``work_events``, which requires materialized
insight products that are frequently absent (devshell: polylogue:missing).
This tier instead reads ``sessions`` + ``session_repos`` directly from the
Polylogue index sqlite DB — those tables are populated by ordinary archive
ingestion, no insight materialization needed — and attributes a focus span
to whichever ``/realm/project/*`` checkout the dominant overlapping session
was rooted in.

Coarser than the work_event tier (one session interval covers its whole
[created, updated] range, not per-turn activity), so long-lived resumed
sessions are a real precision risk: a session reopened weeks after it was
created still reports its original ``created_at_ms``, making its interval
span the idle gap too. The day-bucketed index bounds the blast radius to a
session's touched calendar days, and the same confidence-floor gate used by
the work_event tier keeps low-overlap matches out.
"""
from __future__ import annotations

import functools
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Protocol, Sequence

from ..core.primitives import split_by_day

__all__ = [
    "SessionRepoInterval",
    "SessionOverlapAttribution",
    "SpanWindow",
    "session_repo_intervals",
    "attribute_spans_by_session_overlap",
]

_DEFAULT_CONFIDENCE_FLOOR = 0.3
_DEFAULT_SLACK_S = 30.0


class SpanWindow(Protocol):
    """Anything with start/end datetimes — typically an AW focus span."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class SessionRepoInterval:
    """One session's [created, updated] window rooted in one repo checkout."""

    session_id: str
    project: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class SessionOverlapAttribution:
    project: str
    session_id: str
    overlap_s: float
    confidence: float


def _project_from_root_path(root_path: str) -> str | None:
    if not root_path:
        return None
    name = PurePosixPath(root_path).name
    return name or None


@functools.lru_cache(maxsize=8)
def session_repo_intervals(db_path: str) -> tuple[SessionRepoInterval, ...]:
    """Read (session, project, [created,updated]) triples from the index DB.

    Cached per ``db_path`` for the process lifetime — the index DB is
    append-mostly and re-querying it per AW window would dominate the
    attribution path. Call ``session_repo_intervals.cache_clear()`` in
    tests that swap the underlying DB file.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT sr.session_id, sr.root_path, s.created_at_ms, s.updated_at_ms
            FROM session_repos sr
            JOIN sessions s ON s.session_id = sr.session_id
            WHERE sr.root_path LIKE '/realm/project/%'
              AND s.created_at_ms IS NOT NULL
              AND s.updated_at_ms IS NOT NULL
              AND s.updated_at_ms >= s.created_at_ms
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[SessionRepoInterval] = []
    for session_id, root_path, created_ms, updated_ms in rows:
        project = _project_from_root_path(root_path)
        if not project:
            continue
        start = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
        if end <= start:
            continue
        out.append(
            SessionRepoInterval(session_id=session_id, project=project, start=start, end=end)
        )
    return tuple(out)


def _day_index(
    intervals: Sequence[SessionRepoInterval],
) -> dict[date, list[SessionRepoInterval]]:
    """Bucket session intervals by every logical day they touch.

    An interval-tree substitute: spans only need to compare against
    sessions that actually touch their day, instead of scanning all
    session_repos rows per span.
    """
    index: dict[date, list[SessionRepoInterval]] = defaultdict(list)
    for interval in intervals:
        for day, _seg in split_by_day(interval.start, interval.end):
            index[day].append(interval)
    return index


def attribute_spans_by_session_overlap(
    spans: Sequence[SpanWindow],
    intervals: Sequence[SessionRepoInterval],
    *,
    slack_s: float = _DEFAULT_SLACK_S,
    confidence_floor: float = _DEFAULT_CONFIDENCE_FLOOR,
) -> list[SessionOverlapAttribution | None]:
    """Best dominant-overlap session→project attribution per span, in order.

    ``None`` when no session's interval overlaps the span at all, or the
    best overlap's confidence (overlap_s / span_duration_s) falls below
    ``confidence_floor`` — e.g. a span that only grazes a multi-day
    resumed session's idle tail.
    """
    index = _day_index(intervals)
    slack = timedelta(seconds=slack_s)
    results: list[SessionOverlapAttribution | None] = []
    for span in spans:
        span_start, span_end = span.start, span.end
        span_dur_s = max((span_end - span_start).total_seconds(), 0.001)

        candidates: dict[str, SessionRepoInterval] = {}
        for day, _seg in split_by_day(span_start, span_end):
            for interval in index.get(day, ()):
                candidates[interval.session_id] = interval

        best: SessionOverlapAttribution | None = None
        for interval in candidates.values():
            iv_start, iv_end = interval.start - slack, interval.end + slack
            if iv_start > span_end or iv_end < span_start:
                continue
            overlap_start = max(span_start, interval.start)
            overlap_end = min(span_end, interval.end)
            overlap_s = max((overlap_end - overlap_start).total_seconds(), 0.0)
            if overlap_s == 0.0:
                # Slack-only overlap still counts, at nominal weight — ranks
                # below any real intersection but above no-overlap at all.
                overlap_s = 0.1
            if best is None or overlap_s > best.overlap_s:
                confidence = min(overlap_s / span_dur_s, 1.0)
                best = SessionOverlapAttribution(
                    project=interval.project,
                    session_id=interval.session_id,
                    overlap_s=overlap_s,
                    confidence=confidence,
                )

        if best is not None and best.confidence < confidence_floor:
            best = None
        results.append(best)
    return results
