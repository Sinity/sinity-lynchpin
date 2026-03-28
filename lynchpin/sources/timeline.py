"""Cross-source timeline: chronological interleave of all activity sources.

The single most useful function for narrative generation — answers "what was I doing
at any given moment?" by merging AW focus spans, git commits, terminal commands,
chat sessions, and web visits into one sorted stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, Sequence

from ..core.primitives import date_to_dt_range

__all__ = [
    "TimelineEvent",
    "WorkSession",
    "timeline",
    "work_sessions",
]


@dataclass(frozen=True)
class TimelineEvent:
    start: datetime
    end: datetime
    source: str       # "aw", "git", "terminal", "chat", "web", "sleep"
    kind: str         # source-specific: "focus", "commit", "command", "session", "visit", "sleep"
    summary: str      # human-readable: "Coding sinex in VS Code", "git commit: fix auth"
    project: str | None
    mode: str | None


def timeline(*, start: date, end: date) -> list[TimelineEvent]:
    """Chronological interleave of all sources over a date range.

    Returns events sorted by start time. Each event is a self-contained
    description of what was happening, tagged with source, project, and mode.
    """
    s_dt, e_dt = date_to_dt_range(start, end)
    events: list[TimelineEvent] = []

    events.extend(_aw_events(s_dt, e_dt))
    events.extend(_git_events(start, end))
    events.extend(_terminal_events(s_dt, e_dt))
    events.extend(_chat_events(start, end))
    events.extend(_sleep_events(start, end))

    events.sort(key=lambda e: e.start)
    return events


def _aw_events(s_dt: datetime, e_dt: datetime) -> Iterator[TimelineEvent]:
    try:
        from .activitywatch import app_sessions
    except Exception:
        return
    for sess in app_sessions(start=s_dt, end=e_dt):
        parts = []
        if sess.mode:
            parts.append(sess.mode.capitalize())
        if sess.project:
            parts.append(sess.project)
        if sess.app:
            parts.append(f"in {sess.app}")
        summary = " ".join(parts) if parts else "Active"
        yield TimelineEvent(
            start=sess.start, end=sess.end, source="aw", kind="focus",
            summary=summary, project=sess.project, mode=sess.mode,
        )


def _git_events(start: date, end: date) -> Iterator[TimelineEvent]:
    try:
        from .git import commit_facts
    except Exception:
        return
    for f in commit_facts(start=start, end=end):
        summary = f"commit: {f.subject[:80]}" if f.subject else "commit"
        yield TimelineEvent(
            start=f.authored_at, end=f.authored_at + timedelta(seconds=1),
            source="git", kind="commit", summary=summary,
            project=f.repo, mode="coding",
        )


def _terminal_events(s_dt: datetime, e_dt: datetime) -> Iterator[TimelineEvent]:
    try:
        from .terminal import shell_sessions
    except Exception:
        return
    for sess in shell_sessions(start=s_dt, end=e_dt):
        parts = [f"{sess.command_count} commands"]
        if sess.project:
            parts.append(f"in {sess.project}")
        if sess.category:
            parts.append(f"({sess.category})")
        yield TimelineEvent(
            start=sess.start, end=sess.end, source="terminal", kind="session",
            summary=" ".join(parts), project=sess.project, mode="shell",
        )


def _chat_events(start: date, end: date) -> Iterator[TimelineEvent]:
    try:
        from .polylogue import iter_session_profiles
    except Exception:
        return
    for p in iter_session_profiles():
        if p.first_message_at is None:
            continue
        d = p.first_message_at.date()
        if d < start or d > end:
            continue
        end_dt = p.last_message_at or p.first_message_at + timedelta(minutes=5)
        parts = [f"{p.provider} chat"]
        if p.title:
            parts.append(f"'{p.title[:50]}'")
        parts.append(f"({p.message_count} msgs)")
        yield TimelineEvent(
            start=p.first_message_at, end=end_dt, source="chat", kind="session",
            summary=" ".join(parts),
            project=p.work_event_projects[0] if p.work_event_projects else None,
            mode="chat",
        )


def _sleep_events(start: date, end: date) -> Iterator[TimelineEvent]:
    try:
        from .sleep import entries_in_range
    except Exception:
        return
    for e in entries_in_range(start, end):
        hours = round(e.total_minutes / 60, 1)
        quality = f", {e.quality_label}" if e.avg_score is not None else ""
        for seg in e.segments:
            if seg.start and seg.end and seg.start != datetime.min:
                yield TimelineEvent(
                    start=seg.start, end=seg.end, source="sleep", kind="sleep",
                    summary=f"Sleep {hours}h{quality}",
                    project=None, mode=None,
                )
                break  # one event per sleep entry


# ══════════════════════════════════════════════════════════════════════════════
# Work session reconstruction
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WorkSession:
    project: str
    start: datetime
    end: datetime
    duration_min: float
    events: tuple[TimelineEvent, ...]
    source_breakdown: dict[str, int]  # source → event count


def work_sessions(
    *, start: date, end: date, project: str | None = None, min_duration_min: float = 10,
) -> list[WorkSession]:
    """Reconstruct coherent work sessions from cross-source timeline.

    Groups timeline events by project into sessions with max 30-min gaps.
    Returns sessions sorted by start time, optionally filtered to one project.
    """
    events = timeline(start=start, end=end)

    # Group events by project
    by_project: dict[str, list[TimelineEvent]] = {}
    for e in events:
        p = e.project or "__unattributed__"
        if project and p != project:
            continue
        by_project.setdefault(p, []).append(e)

    result: list[WorkSession] = []
    for proj, proj_events in by_project.items():
        if proj == "__unattributed__":
            continue
        proj_events.sort(key=lambda e: e.start)

        # Group into sessions with max 30-min gaps
        session_events: list[TimelineEvent] = [proj_events[0]]
        for e in proj_events[1:]:
            gap = (e.start - session_events[-1].end).total_seconds() / 60
            if gap <= 30:
                session_events.append(e)
            else:
                _maybe_add_session(result, proj, session_events, min_duration_min)
                session_events = [e]
        _maybe_add_session(result, proj, session_events, min_duration_min)

    result.sort(key=lambda s: s.start)
    return result


def _maybe_add_session(
    result: list[WorkSession], project: str, events: list[TimelineEvent], min_dur: float,
) -> None:
    if not events:
        return
    start = events[0].start
    end = max(e.end for e in events)
    dur = (end - start).total_seconds() / 60
    if dur < min_dur:
        return
    breakdown: dict[str, int] = {}
    for e in events:
        breakdown[e.source] = breakdown.get(e.source, 0) + 1
    result.append(WorkSession(
        project=project, start=start, end=end,
        duration_min=round(dur, 1), events=tuple(events),
        source_breakdown=breakdown,
    ))
