"""AW-like timeline and time-composition views for Polylogue sessions.

This module consumes Polylogue's public facade only. It does not read
Polylogue's legacy SQLite tables directly; unavailable products surface as
``PolylogueMaterializationError`` or degraded composition rows.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from lynchpin.core.parse import as_local, parse_datetime
from lynchpin.sources.polylogue import (
    PolylogueMaterializationError,
    _polylogue_client,
    _session_profile_from_insight,
    session_profiles_for_date,
    work_events,
)
from lynchpin.sources.polylogue_models import SessionProfile
from lynchpin.sources.polylogue_timeline_models import (
    PolylogueCrossSourceOverlap,
    PolylogueSessionComposition,
    PolylogueTimelineSpan,
)

__all__ = [
    "PolylogueTimelineSpan",
    "PolylogueCrossSourceOverlap",
    "PolylogueSessionComposition",
    "session_timeline",
    "session_composition",
    "session_compositions",
    "timeline_overlaps",
]

CrossSourceProvider = Callable[[SessionProfile], Iterable[PolylogueTimelineSpan]]


@dataclass(frozen=True)
class _RawMessage:
    message_id: str
    role: str
    timestamp: datetime | None
    has_tool_use: bool
    has_thinking: bool
    tool_names: tuple[str, ...]
    word_count: int
    message_type: str | None


def session_timeline(
    session_id: str,
    *,
    include_cross_source: bool = True,
    cross_source_provider: CrossSourceProvider | None = None,
) -> list[PolylogueTimelineSpan]:
    """Return chronological timeline spans for one Polylogue session."""

    profile = _profile_for_session(session_id)
    native = _native_spans(profile)
    if not include_cross_source:
        return sorted(native, key=_span_sort_key)
    external = list(
        (cross_source_provider or _default_cross_source_spans)(profile)
    )
    return sorted([*native, *external], key=_span_sort_key)


def session_composition(
    session_id: str,
    *,
    include_cross_source: bool = True,
    cross_source_provider: CrossSourceProvider | None = None,
) -> PolylogueSessionComposition:
    """Summarize one session's time by lane/kind and external overlap."""

    try:
        profile = _profile_for_session(session_id)
        spans = _native_spans(profile)
        external = (
            list((cross_source_provider or _default_cross_source_spans)(profile))
            if include_cross_source
            else []
        )
        overlaps = timeline_overlaps(spans, external)
        return _composition_from(profile, spans, overlaps, status="ok", reason=None)
    except PolylogueMaterializationError as exc:
        return PolylogueSessionComposition(
            session_id=session_id,
            provider="unknown",
            title="",
            start=None,
            end=None,
            status="unavailable",
            reason=str(exc),
            message_count=0,
            wall_seconds=0.0,
            engaged_seconds=0.0,
            span_count=0,
            overlap_count=0,
            seconds_by_lane={},
            seconds_by_kind={},
            cross_source_seconds={},
        )


def session_compositions(
    *,
    start: date,
    end: date,
    limit: int | None = None,
    include_cross_source: bool = True,
    cross_source_provider: CrossSourceProvider | None = None,
) -> list[PolylogueSessionComposition]:
    """Date-bounded session time compositions."""

    profiles = session_profiles_for_date(start=start, end=end)
    if limit is not None:
        profiles = profiles[:limit]
    rows: list[PolylogueSessionComposition] = []
    for profile in profiles:
        try:
            spans = _native_spans(profile)
            external = (
                list((cross_source_provider or _default_cross_source_spans)(profile))
                if include_cross_source
                else []
            )
            rows.append(
                _composition_from(
                    profile,
                    spans,
                    timeline_overlaps(spans, external),
                    status="ok",
                    reason=None,
                )
            )
        except PolylogueMaterializationError as exc:
            rows.append(
                _composition_from(
                    profile,
                    (),
                    (),
                    status="unavailable",
                    reason=str(exc),
                )
            )
    return rows


def timeline_overlaps(
    native_spans: Iterable[PolylogueTimelineSpan],
    external_spans: Iterable[PolylogueTimelineSpan],
) -> list[PolylogueCrossSourceOverlap]:
    """Compute positive-duration overlaps between native and external lanes."""

    native = [
        span
        for span in native_spans
        if span.duration_s > 0 and span.lane in {"message_gap", "semantic"}
    ]
    external = [span for span in external_spans if span.duration_s > 0]
    overlaps: list[PolylogueCrossSourceOverlap] = []
    for left in native:
        for right in external:
            start = max(left.start, right.start)
            end = min(left.end, right.end)
            duration = (end - start).total_seconds()
            if duration <= 0:
                continue
            overlaps.append(
                PolylogueCrossSourceOverlap(
                    session_id=left.session_id,
                    primary_span_id=left.span_id,
                    other_span_id=right.span_id,
                    source=right.source,
                    lane=right.lane,
                    kind=right.kind,
                    start=start,
                    end=end,
                    duration_s=duration,
                    project=right.project,
                    metadata={"primary_kind": left.kind},
                )
            )
    return overlaps


def _profile_for_session(session_id: str) -> SessionProfile:
    try:
        insight = _polylogue_client().get_session_profile_insight(session_id)
    except Exception as exc:
        raise PolylogueMaterializationError(
            f"Polylogue session profile read failed for {session_id}: {exc}"
        ) from exc
    if insight is None:
        raise PolylogueMaterializationError(
            f"Polylogue session profile missing for {session_id}"
        )
    return _session_profile_from_insight(insight)


def _native_spans(profile: SessionProfile) -> list[PolylogueTimelineSpan]:
    spans: list[PolylogueTimelineSpan] = []
    messages = _messages(profile.conversation_id)
    spans.extend(_message_marker_spans(profile, messages))
    spans.extend(_message_gap_spans(profile, messages))
    spans.extend(_work_event_spans(profile))
    spans.extend(_phase_spans(profile))
    return sorted(spans, key=_span_sort_key)


def _messages(session_id: str) -> list[_RawMessage]:
    rows: list[Any] = []
    offset = 0
    limit = 500
    try:
        while True:
            chunk = _polylogue_client().get_messages_paginated(
                session_id,
                limit=limit,
                offset=offset,
            )
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < limit:
                break
            offset += limit
    except Exception as exc:
        raise PolylogueMaterializationError(
            f"Polylogue message read failed for {session_id}: {exc}"
        ) from exc

    out = [_raw_message(row) for row in rows]
    return sorted(
        out,
        key=lambda item: (
            item.timestamp or datetime.min.replace(tzinfo=profile_tz_fallback()),
            item.message_id,
        ),
    )


def profile_tz_fallback() -> Any:
    from lynchpin.core.parse import local_tz

    return local_tz()


def _raw_message(row: Any) -> _RawMessage:
    blocks = [
        block
        for block in (getattr(row, "content_blocks", None) or ())
        if isinstance(block, dict)
    ]
    tool_names = tuple(
        str(block.get("tool_name") or block.get("name"))
        for block in blocks
        if block.get("type") in {"tool_use", "tool_result"}
        and (block.get("tool_name") or block.get("name"))
    )
    text = str(getattr(row, "text", "") or "")
    ts = getattr(row, "timestamp", None)
    if isinstance(ts, str):
        ts = parse_datetime(ts)
    return _RawMessage(
        message_id=str(getattr(row, "id", "") or f"message:{id(row)}"),
        role=str(getattr(row, "role", "unknown") or "unknown"),
        timestamp=as_local(ts) if ts is not None else None,
        has_tool_use=bool(getattr(row, "has_tool_use", False))
        or any(block.get("type") in {"tool_use", "tool_result"} for block in blocks),
        has_thinking=bool(getattr(row, "has_thinking", False))
        or any(block.get("type") == "thinking" for block in blocks),
        tool_names=tool_names,
        word_count=len(text.split()) if text else 0,
        message_type=(
            str(getattr(row, "message_type"))
            if getattr(row, "message_type", None) is not None
            else None
        ),
    )


def _message_marker_spans(
    profile: SessionProfile,
    messages: Iterable[_RawMessage],
) -> list[PolylogueTimelineSpan]:
    spans = []
    for idx, msg in enumerate(messages):
        if msg.timestamp is None:
            continue
        kind = _message_kind(msg)
        spans.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:message:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="message",
                kind=kind,
                start=msg.timestamp,
                end=msg.timestamp,
                source="polylogue.message",
                role=msg.role,
                tool_names=msg.tool_names,
                fidelity="point",
                metadata={
                    "message_id": msg.message_id,
                    "word_count": msg.word_count,
                    "message_type": msg.message_type,
                    "has_thinking": msg.has_thinking,
                    "has_tool_use": msg.has_tool_use,
                },
            )
        )
    return spans


def _message_gap_spans(
    profile: SessionProfile,
    messages: list[_RawMessage],
) -> list[PolylogueTimelineSpan]:
    timed = [msg for msg in messages if msg.timestamp is not None]
    spans = []
    for idx, (prev, nxt) in enumerate(zip(timed, timed[1:])):
        if prev.timestamp is None or nxt.timestamp is None:
            continue
        if nxt.timestamp <= prev.timestamp:
            continue
        spans.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:message_gap:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="message_gap",
                kind=_transition_kind(prev, nxt),
                start=prev.timestamp,
                end=nxt.timestamp,
                source="polylogue.message_transition",
                role=f"{prev.role}->{nxt.role}",
                fidelity="bounded_by_adjacent_messages",
                confidence=0.75,
                metadata={
                    "from_message_id": prev.message_id,
                    "to_message_id": nxt.message_id,
                    "from_has_tool_use": prev.has_tool_use,
                    "to_has_tool_use": nxt.has_tool_use,
                    "from_has_thinking": prev.has_thinking,
                    "to_has_thinking": nxt.has_thinking,
                },
            )
        )
    return spans


def _message_kind(msg: _RawMessage) -> str:
    if msg.role == "assistant" and msg.has_tool_use:
        return "assistant_tool_use_message"
    if msg.role == "assistant" and msg.has_thinking:
        return "assistant_thinking_message"
    if msg.role == "user":
        return "user_message"
    return f"{msg.role}_message"


def _transition_kind(prev: _RawMessage, nxt: _RawMessage) -> str:
    if prev.has_tool_use or nxt.has_tool_use or "tool" in {prev.role, nxt.role}:
        return "tool_wait_or_result"
    if prev.role == "user" and nxt.role == "assistant":
        return "assistant_response_wait"
    if prev.role == "assistant" and nxt.role == "user":
        return "user_gap_or_composition"
    return "message_gap"


def _work_event_spans(profile: SessionProfile) -> list[PolylogueTimelineSpan]:
    start_date = (profile.first_message_at or profile.last_message_at)
    end_date = profile.last_message_at or profile.first_message_at
    if start_date is None or end_date is None:
        return []
    rows = [
        ev
        for ev in work_events(start=start_date.date(), end=end_date.date())
        if ev.conversation_id == profile.conversation_id and ev.start and ev.end
    ]
    spans = []
    for ev in rows:
        spans.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:work_event:{ev.event_id}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="semantic",
                kind=ev.kind,
                start=as_local(ev.start),
                end=as_local(ev.end),
                source="polylogue.work_event",
                project=profile.work_event_projects[0]
                if profile.work_event_projects
                else None,
                summary=ev.summary,
                tool_names=ev.tools_used,
                confidence=ev.confidence,
                metadata={"file_paths": list(ev.file_paths)},
            )
        )
    return spans


def _phase_spans(profile: SessionProfile) -> list[PolylogueTimelineSpan]:
    try:
        rows = _polylogue_client().get_session_phase_insights(profile.conversation_id)
    except Exception:
        return []
    spans = []
    for idx, row in enumerate(rows or ()):
        evidence = getattr(row, "evidence", None)
        inference = getattr(row, "inference", None)
        start = parse_datetime(getattr(evidence, "start_time", None))
        end = parse_datetime(getattr(evidence, "end_time", None))
        if start is None or end is None or end < start:
            continue
        kind = (
            getattr(row, "kind", None)
            or getattr(inference, "kind", None)
            or getattr(evidence, "kind", None)
            or "phase"
        )
        confidence = float(getattr(inference, "confidence", 1.0) or 1.0)
        spans.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:phase:{getattr(row, 'phase_id', idx)}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="semantic",
                kind=str(kind),
                start=as_local(start),
                end=as_local(end),
                source="polylogue.phase",
                confidence=max(0.0, min(confidence, 1.0)),
                fidelity=str(getattr(evidence, "timing_provenance", "") or "inferred"),
                summary=str(getattr(inference, "summary", "") or "") or None,
            )
        )
    return spans


def _default_cross_source_spans(profile: SessionProfile) -> list[PolylogueTimelineSpan]:
    if profile.first_message_at is None or profile.last_message_at is None:
        return []
    start = as_local(profile.first_message_at)
    end = as_local(profile.last_message_at)
    if end <= start:
        return []
    spans: list[PolylogueTimelineSpan] = []
    for loader in (
        _activitywatch_spans,
        _keylog_spans,
        _terminal_spans,
        _git_spans,
        _service_spans,
    ):
        try:
            spans.extend(loader(profile, start, end))
        except Exception:
            continue
    return spans


def _activitywatch_spans(
    profile: SessionProfile,
    start: datetime,
    end: datetime,
) -> list[PolylogueTimelineSpan]:
    from lynchpin.sources.activitywatch import focus_timeline

    out = []
    for idx, span in enumerate(focus_timeline(start=start, end=end)):
        out.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:aw:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="activitywatch",
                kind=span.kind,
                start=span.start,
                end=span.end,
                source="activitywatch.focus_timeline",
                project=span.project,
                app=span.app,
                summary=span.title,
                metadata={
                    "mode": span.mode,
                    "keylog_state": span.keylog_state,
                    "keypress_count": span.keypress_count,
                },
            )
        )
    return out


def _keylog_spans(
    profile: SessionProfile,
    start: datetime,
    end: datetime,
) -> list[PolylogueTimelineSpan]:
    from lynchpin.sources.keylog import keypresses

    presses = sorted(keypresses(start=start, end=end), key=lambda ev: ev.ts)
    if not presses:
        return []
    groups: list[list[Any]] = [[presses[0]]]
    for press in presses[1:]:
        if (press.ts - groups[-1][-1].ts).total_seconds() <= 30:
            groups[-1].append(press)
        else:
            groups.append([press])
    out = []
    for idx, group in enumerate(groups):
        span_start = group[0].ts
        span_end = max(group[-1].ts, span_start + timedelta(seconds=1))
        out.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:keylog:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="keylog",
                kind="typing_burst",
                start=span_start,
                end=span_end,
                source="keylog.keypresses",
                metadata={"keypress_count": len(group)},
            )
        )
    return out


def _terminal_spans(
    profile: SessionProfile,
    start: datetime,
    end: datetime,
) -> list[PolylogueTimelineSpan]:
    from lynchpin.sources.terminal import shell_sessions

    out = []
    for idx, shell in enumerate(shell_sessions(start=start, end=end)):
        out.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:terminal:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="terminal",
                kind=shell.category,
                start=shell.start,
                end=shell.end,
                source="terminal.shell_sessions",
                project=shell.project,
                summary=", ".join(shell.commands_summary),
                metadata={
                    "cwd": shell.cwd,
                    "command_count": shell.command_count,
                    "error_count": shell.error_count,
                },
            )
        )
    return out


def _git_spans(
    profile: SessionProfile,
    start: datetime,
    end: datetime,
) -> list[PolylogueTimelineSpan]:
    from lynchpin.sources.git import commit_sessions

    out = []
    for idx, commit in enumerate(commit_sessions(start=start.date(), end=end.date())):
        commit_start = as_local(commit.start)
        commit_end = as_local(commit.end)
        if commit_end < start or commit_start > end:
            continue
        out.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:git:{idx}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="git",
                kind="commit_session",
                start=max(commit_start, start),
                end=min(max(commit_end, commit_start + timedelta(seconds=1)), end),
                source="git.commit_sessions",
                project=commit.repo,
                metadata={
                    "commit_count": commit.commit_count,
                    "lines_changed": commit.lines_changed,
                    "ai_fraction": commit.ai_fraction,
                },
            )
        )
    return out


def _service_spans(
    profile: SessionProfile,
    start: datetime,
    end: datetime,
) -> list[PolylogueTimelineSpan]:
    from lynchpin.sources.machine import service_states
    from lynchpin.sources.service_health import downtime_intervals

    states = list(service_states(start=start.date(), end=end.date()))
    out = []
    for idx, down in enumerate(
        downtime_intervals(states, window_start=start, window_end=end)
    ):
        out.append(
            PolylogueTimelineSpan(
                span_id=f"{profile.conversation_id}:service:{idx}:{down.unit}",
                session_id=profile.conversation_id,
                provider=profile.provider,
                lane="service_health",
                kind=down.kind,
                start=down.start,
                end=down.end,
                source="service_health.downtime_intervals",
                summary=down.unit,
                metadata={"unit": down.unit, "states": list(down.observed_states)},
            )
        )
    return out


def _composition_from(
    profile: SessionProfile,
    spans: Iterable[PolylogueTimelineSpan],
    overlaps: Iterable[PolylogueCrossSourceOverlap],
    *,
    status: str,
    reason: str | None,
) -> PolylogueSessionComposition:
    span_rows = tuple(spans)
    overlap_rows = tuple(overlaps)
    by_lane: dict[str, float] = defaultdict(float)
    by_kind: dict[str, float] = defaultdict(float)
    for span in span_rows:
        if span.duration_s <= 0:
            continue
        by_lane[span.lane] += span.duration_s
        by_kind[span.kind] += span.duration_s
    cross: dict[str, float] = defaultdict(float)
    for row in overlap_rows:
        cross[row.source] += row.duration_s
    start = as_local(profile.first_message_at) if profile.first_message_at else None
    end = as_local(profile.last_message_at) if profile.last_message_at else None
    wall_seconds = float(profile.wall_duration_ms or 0) / 1000.0
    if wall_seconds <= 0 and start is not None and end is not None:
        wall_seconds = max((end - start).total_seconds(), 0.0)
    return PolylogueSessionComposition(
        session_id=profile.conversation_id,
        provider=profile.provider,
        title=profile.title,
        start=start,
        end=end,
        status=status,
        reason=reason,
        message_count=profile.message_count,
        wall_seconds=wall_seconds,
        engaged_seconds=float(profile.engaged_duration_ms or 0) / 1000.0,
        span_count=len(span_rows),
        overlap_count=len(overlap_rows),
        seconds_by_lane={key: round(value, 3) for key, value in sorted(by_lane.items())},
        seconds_by_kind={key: round(value, 3) for key, value in sorted(by_kind.items())},
        cross_source_seconds={key: round(value, 3) for key, value in sorted(cross.items())},
        projects=profile.work_event_projects,
        tags=profile.auto_tags,
    )


def _span_sort_key(span: PolylogueTimelineSpan) -> tuple[datetime, str, str]:
    return (span.start, span.lane, span.span_id)
