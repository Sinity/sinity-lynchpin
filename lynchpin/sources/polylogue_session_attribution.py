"""Session/repo time-overlap attribution via Polylogue session summaries.

Fallback tier for ``activitywatch._enrich_with_polylogue``: the primary tier
attributes spans via polylogue ``work_events``, which requires materialized
insight products that are frequently absent (devshell: polylogue:missing).
This tier reads the typed archive summary facade, available after ordinary
ingestion without insight materialization, and attributes a focus span
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
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol, Sequence

from ..core.cache import files_signature
from ..core.errors import SourceUnavailableError
from ..core.primitives import split_by_day
from .polylogue_client import _polylogue_client

__all__ = [
    "SessionRepoInterval",
    "SessionOverlapAttribution",
    "SpanWindow",
    "session_repo_intervals",
    "attribute_spans_by_session_overlap",
]

_DEFAULT_CONFIDENCE_FLOOR = 0.3
_DEFAULT_SLACK_S = 30.0
_SUMMARY_LIMIT = 1_000_000


class SpanWindow(Protocol):
    """Anything with start/end datetimes — typically an AW focus span."""

    @property
    def start(self) -> datetime: ...

    @property
    def end(self) -> datetime: ...


@dataclass(frozen=True)
class SessionRepoInterval:
    """One session's [created, updated] window and its reported directory."""

    session_id: str
    project: str
    start: datetime
    end: datetime
    provenance: str = "polylogue.session_summary.working_directories"


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


def session_repo_intervals(db_path: str) -> tuple[SessionRepoInterval, ...]:
    """Read session intervals from Polylogue's typed archive summaries.

    Invalidate the cached intervals when the database or its WAL changes.
    """
    path = Path(db_path)
    if not path.exists():
        raise SourceUnavailableError("polylogue", path=db_path, reason="archive index absent")
    signature = files_signature((path, Path(f"{path}-wal")))
    return _session_repo_intervals_cached(db_path, signature)


@functools.lru_cache(maxsize=8)
def _session_repo_intervals_cached(
    db_path: str, _signature: object
) -> tuple[SessionRepoInterval, ...]:
    try:
        # Explicit limit overrides the facade's default single-page cap.
        summaries = _polylogue_client().list_summaries(limit=_SUMMARY_LIMIT)
    except Exception as exc:
        raise SourceUnavailableError(
            "polylogue", path=db_path, reason=f"session summaries unavailable: {exc}"
        ) from exc
    if len(summaries) >= _SUMMARY_LIMIT:
        raise SourceUnavailableError(
            "polylogue", path=db_path, reason="session summary limit reached"
        )

    out: list[SessionRepoInterval] = []
    for summary in summaries:
        start, end = summary.created_at, summary.updated_at
        if start is None or end is None or end <= start:
            continue
        for root_path in summary.working_directories:
            if not root_path.startswith("/realm/project/"):
                continue
            project = _project_from_root_path(root_path)
            if project:
                out.append(SessionRepoInterval(
                    session_id=str(summary.id), project=project, start=start, end=end,
                ))
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
