"""Per-source shared signal iterators and AW event coalescing logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence

from ..sources.captures import activitywatch, atuin
from ..sources.captures.terminal_capture import (
    TerminalSessionEvent,
    TerminalSessionMetadata,
    iter_terminal_session_events,
    iter_terminal_sessions_fast,
)
from ..sources.indices import gitstats
from . import (
    ActivitySignal,
    _as_local,
    _domain_from_url,
    _parse_optional_dt,
    _path_from_window_title,
    _project_hint_from_paths,
    _project_hint_from_text,
    _signal_id,
    _text,
)
from .loader import (
    _AW_AFk_ARTEFACT_DIR,
    _AW_WEB_ARTEFACT_DIR,
    _AW_WINDOW_ARTEFACT_DIR,
    _GIT_ARTEFACT,
    _POLYLOGUE_ARTEFACT,
    _SESSION_EVENTS_ARTEFACT,
    _SESSIONS_ARTEFACT,
    _artefact_cutover,
    _aw_monthly_artefact_cutover,
    _aw_signals_mixed,
    _iter_aw_signals_from_monthly_jsonl,
    _iter_events_from_jsonl,
    _iter_git_signals_from_jsonl,
    _iter_months,
    _iter_polylogue_signals_from_jsonl,
    _iter_sessions_from_jsonl,
)

_AW_SAMPLE_CAP = timedelta(seconds=15)
_AW_SAMPLE_TAIL = timedelta(seconds=5)
_AW_COLLAPSE_GAP = timedelta(seconds=30)
_POINT_SIGNAL_SECONDS = timedelta(seconds=30)
_TRANSCRIPT_SIGNAL_SECONDS = timedelta(minutes=5)
_TERMINAL_COMMAND_SECONDS = timedelta(seconds=5)


def _iter_all_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    yield from _window_signals(start, end)
    yield from _web_signals(start, end)
    yield from _afk_signals(start, end)
    yield from _atuin_signals(start, end)
    yield from _terminal_session_signals(start, end)
    yield from _terminal_command_signals(start, end)
    yield from _polylogue_session_signals(start, end)
    yield from _git_commit_signals(start, end)


def _window_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    yield from _aw_signals_mixed(
        _AW_WINDOW_ARTEFACT_DIR,
        start,
        end,
        source="activitywatch.window",
        kind="window",
        aw_fetch=activitywatch.window_events,
        app_key="app",
        title_key="title",
        url_key=None,
    )


def _web_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    yield from _aw_signals_mixed(
        _AW_WEB_ARTEFACT_DIR,
        start,
        end,
        source="activitywatch.web",
        kind="web",
        aw_fetch=activitywatch.web_events,
        app_key="browser",
        title_key="title",
        url_key="url",
    )


def _collapse_window_like(
    *,
    source: str,
    kind: str,
    events: Sequence[activitywatch.ActivityWatchEvent],
    app_key: Optional[str],
    title_key: Optional[str],
    url_key: Optional[str],
) -> Iterator[ActivitySignal]:
    # Accumulate into mutable locals during a run; create ActivitySignal only at yield.
    cur_app: Optional[str] = None
    cur_title: Optional[str] = None
    cur_url: Optional[str] = None
    cur_domain: Optional[str] = None
    cur_project_hint: Optional[str] = None
    cur_start: Optional[datetime] = None
    cur_end: Optional[datetime] = None
    cur_bucket: Optional[str] = None
    cur_signal_id: Optional[str] = None
    sample_count = 0

    def _emit() -> ActivitySignal:
        assert cur_signal_id is not None
        assert cur_start is not None
        assert cur_end is not None
        return ActivitySignal(
            signal_id=cur_signal_id,
            source=source,
            kind=kind,
            start=cur_start,
            end=cur_end,
            app=cur_app,
            title=cur_title,
            url=cur_url,
            domain=cur_domain,
            project_hint=cur_project_hint,
            evidence={"sample_count": sample_count, "bucket": cur_bucket},
        )

    for index, event in enumerate(events):
        ev_start = _as_local(event.start)
        raw_end = _as_local(event.end)
        next_start = _as_local(events[index + 1].start) if index + 1 < len(events) else None
        effective_end = raw_end if raw_end > ev_start else None
        if effective_end is None:
            if next_start and next_start > ev_start:
                effective_end = min(next_start, ev_start + _AW_SAMPLE_CAP)
            else:
                effective_end = ev_start + _AW_SAMPLE_TAIL
        payload = event.data or {}
        app = _text(payload.get(app_key)) if app_key else None
        title = _text(payload.get(title_key)) if title_key else None
        url = _text(payload.get(url_key)) if url_key else None

        # Collapse check (before computing expensive project_hint)
        if (
            cur_signal_id is not None
            and cur_app == app
            and cur_title == title
            and cur_url == url
            and ev_start <= cur_end + _AW_COLLAPSE_GAP  # type: ignore[operator]
        ):
            sample_count += 1
            if cur_end is None or effective_end > cur_end:
                cur_end = effective_end
            continue

        # Yield previous run
        if cur_signal_id is not None:
            yield _emit()

        # Start new run
        domain = _domain_from_url(url)
        project_hint = _project_hint_from_paths(
            _path_from_window_title(title),
            _project_hint_from_text(title),
            _project_hint_from_text(url),
        )
        cur_app = app
        cur_title = title
        cur_url = url
        cur_domain = domain
        cur_project_hint = project_hint
        cur_start = ev_start
        cur_end = effective_end
        cur_bucket = event.bucket
        cur_signal_id = _signal_id(source, ev_start, effective_end, app, title, url)
        sample_count = 1

    if cur_signal_id is not None:
        yield _emit()


def _afk_db_signals(
    start: datetime,
    end: datetime,
    seen_ids: set[str],
) -> Iterator[ActivitySignal]:
    """Fetch AFK signals directly from the AW DB for [start, end]."""
    for event in activitywatch.afk_events(start=start, end=end):
        status = _text((event.data or {}).get("status"))
        if status != "afk":
            continue
        signal_start = _as_local(event.start)
        signal_end = max(_as_local(event.end), signal_start)
        sig_id = _signal_id("activitywatch.afk", signal_start, signal_end, status)
        if sig_id in seen_ids:
            continue
        seen_ids.add(sig_id)
        yield ActivitySignal(
            signal_id=sig_id,
            source="activitywatch.afk",
            kind="afk",
            start=signal_start,
            end=signal_end,
            mode_hint="recovery",
            detail=status,
            evidence={"bucket": event.bucket, "status": status},
        )


def _afk_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    from calendar import monthrange as _monthrange

    seen_ids: set[str] = set()
    # Fast path: pre-collapsed monthly JSONL artefacts
    yield from _iter_aw_signals_from_monthly_jsonl(
        _AW_AFk_ARTEFACT_DIR, start, end, seen_signal_ids=seen_ids
    )
    # DB fallback for uncovered months + live overlap for current month
    cutover = _aw_monthly_artefact_cutover(_AW_AFk_ARTEFACT_DIR)
    latest_covered = None
    for year, month in _iter_months(start, end):
        artefact = _AW_AFk_ARTEFACT_DIR / f"{year:04d}-{month:02d}.jsonl"
        if artefact.exists():
            latest_covered = (year, month)
            continue
        # No artefact for this month -- query DB
        _, last_day = _monthrange(year, month)
        m_start = datetime(year, month, 1, tzinfo=timezone.utc)
        m_end = (
            datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
            + timedelta(microseconds=1)
        )
        yield from _afk_db_signals(max(start, m_start), min(end, m_end), seen_ids)
    # Live overlap: AFK events not yet in the artefact.
    # Skip if artefact is fresh (< 1h old) -- DB connection overhead (~0.4s) outweighs benefit.
    # For stale artefacts (> 1h), DB query covers the uncached window.
    if cutover is not None and latest_covered is not None:
        now_utc = datetime.now(timezone.utc)
        if now_utc - cutover > timedelta(hours=1):
            live_start = max(start, cutover - timedelta(minutes=30))
            if live_start < end:
                yield from _afk_db_signals(live_start, end, seen_ids)


def _atuin_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    for command in atuin.iter_commands(start=start, end=end):
        signal_start = _as_local(command.timestamp)
        duration_seconds = 1.0
        if command.duration_ns and command.duration_ns > 0:
            duration_seconds = max(min(command.duration_ns / 1_000_000_000, 900.0), 1.0)
        signal_end = signal_start + timedelta(seconds=duration_seconds)
        yield ActivitySignal(
            signal_id=_signal_id("atuin.command", signal_start, signal_end, command.cwd, command.command),
            source="atuin.command",
            kind="command",
            start=signal_start,
            end=signal_end,
            project_hint=_project_hint_from_paths(command.cwd),
            cwd=command.cwd,
            detail=command.command,
            evidence={
                "duration_ns": command.duration_ns,
                "exit_code": command.exit_code,
            },
        )


def _session_to_signal(
    session: TerminalSessionMetadata,
    start: datetime,
    end: datetime,
) -> Iterator[ActivitySignal]:
    """Convert a TerminalSessionMetadata to an ActivitySignal if it overlaps [start, end]."""
    session_start = _parse_optional_dt(session.created_at)
    if session_start is None:
        return
    session_end = _parse_optional_dt(session.finished_at)
    if session_end is None or session_end < session_start:
        session_end = session_start + timedelta(seconds=max(session.duration_seconds or 0.0, 0.0))
    session_start = _as_local(session_start)
    session_end = _as_local(session_end)
    if session_end <= start or session_start >= end:
        return
    yield ActivitySignal(
        signal_id=_signal_id("instrumentation.terminal_session", session_start, session_end, session.session_id),
        source="instrumentation.terminal_session",
        kind="terminal_session",
        start=max(session_start, start),
        end=min(session_end, end),
        mode_hint="coding" if session.project_root or session.repo_root else None,
        project_hint=_project_hint_from_paths(
            session.final_project_root,
            session.project_root,
            session.final_repo_root,
            session.repo_root,
            session.final_cwd,
            session.start_cwd,
        ),
        cwd=session.final_cwd or session.start_cwd,
        title=session.title,
        detail=session.command,
        evidence={
            "session_id": session.session_id,
            "command_count": session.command_count,
            "active_seconds": session.active_seconds,
            "quality_status": session.quality_status,
        },
    )


def _terminal_session_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    seen_ids: set[str] = set()
    # Fast path: historical sessions from pre-computed JSONL artefact (~5ms for 1000+ sessions)
    for session in _iter_sessions_from_jsonl(_SESSIONS_ARTEFACT, start, end, seen_ids=seen_ids):
        yield from _session_to_signal(session, start, end)
    # Live path: windowed filesystem scan covering only sessions newer than the artefact
    cutover = _artefact_cutover(_SESSIONS_ARTEFACT)
    live_start = max(start, cutover - timedelta(days=1)) if cutover else start
    for session in iter_terminal_sessions_fast(start=live_start, end=end):
        if session.session_id in seen_ids:
            continue
        yield from _session_to_signal(session, start, end)


def _event_to_signal(event: TerminalSessionEvent, start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    """Convert a TerminalSessionEvent to an ActivitySignal if it is a command_start in [start, end]."""
    if event.type != "command_start":
        return
    signal_start = _parse_optional_dt(event.time)
    if signal_start is None:
        return
    signal_start = _as_local(signal_start)
    if signal_start < start or signal_start >= end:
        return
    signal_end = signal_start + _TERMINAL_COMMAND_SECONDS
    command_text = _text(event.payload.get("command") or event.payload.get("cmd"))
    yield ActivitySignal(
        signal_id=_signal_id("instrumentation.terminal_command", signal_start, signal_end, event.session_id, command_text),
        source="instrumentation.terminal_command",
        kind="terminal_command",
        start=signal_start,
        end=signal_end,
        mode_hint="coding" if event.project_root or event.repo_root else None,
        project_hint=_project_hint_from_paths(event.project_root, event.repo_root, event.pwd),
        cwd=event.pwd,
        detail=command_text,
        evidence={
            "session_id": event.session_id,
            "repo_branch": event.repo_branch,
            "repo_commit": event.repo_commit,
            "repo_dirty": event.repo_dirty,
        },
    )


def _terminal_command_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    # Fast path: pre-computed JSONL artefact
    seen_event_ids: set[str] = set()
    for event in _iter_events_from_jsonl(_SESSION_EVENTS_ARTEFACT, start, end):
        eid = f"{event.session_id}:{event.time}:{event.type}"
        seen_event_ids.add(eid)
        yield from _event_to_signal(event, start, end)
    # Live path: recent events not yet in artefact
    cutover = _artefact_cutover(_SESSION_EVENTS_ARTEFACT)
    live_start = max(start, cutover - timedelta(days=1)) if cutover else start
    for event in iter_terminal_session_events(start=live_start, end=end):
        eid = f"{event.session_id}:{event.time}:{event.type}"
        if eid in seen_event_ids:
            continue
        yield from _event_to_signal(event, start, end)


_WORK_EVENT_MODE_MAP = {
    "planning": "planning",
    "implementation": "coding",
    "debugging": "coding",
    "review": "coding",
    "testing": "coding",
    "research": "research",
    "configuration": "coding",
    "documentation": "writing",
    "refactoring": "coding",
    "data_analysis": "research",
    "conversation": "chat",
}


def _profile_to_signals(profile: object) -> Iterator[ActivitySignal]:
    """Convert a SessionProfile to ActivitySignal rows (one per work event, or one chat signal)."""
    if not profile.work_events:  # type: ignore[union-attr]
        signal_start = _as_local(profile.first_message_at or profile.created_at) if (profile.first_message_at or profile.created_at) else None  # type: ignore[union-attr]
        if signal_start is None:
            return
        signal_end = _as_local(profile.last_message_at or profile.updated_at) if (profile.last_message_at or profile.updated_at) else signal_start + _TRANSCRIPT_SIGNAL_SECONDS  # type: ignore[union-attr]
        if signal_end <= signal_start:
            signal_end = signal_start + _TRANSCRIPT_SIGNAL_SECONDS
        yield ActivitySignal(
            signal_id=_signal_id("polylogue.session", signal_start, signal_end, profile.provider, profile.conversation_id),  # type: ignore[union-attr]
            source="polylogue.session",
            kind="session",
            start=signal_start,
            end=signal_end,
            mode_hint="chat",
            project_hint=profile.canonical_projects[0] if profile.canonical_projects else _project_hint_from_paths(*profile.repo_paths),  # type: ignore[union-attr]
            title=profile.title,  # type: ignore[union-attr]
            detail=profile.conversation_id[:12],  # type: ignore[union-attr]
            evidence={
                "provider": profile.provider,  # type: ignore[union-attr]
                "conversation_id": profile.conversation_id,  # type: ignore[union-attr]
                "thread_id": profile.thread_id,  # type: ignore[union-attr]
                "message_count": profile.message_count,  # type: ignore[union-attr]
                "word_count": profile.word_count,  # type: ignore[union-attr]
                "total_cost_usd": profile.total_cost_usd,  # type: ignore[union-attr]
                "tool_categories": profile.tool_categories,  # type: ignore[union-attr]
            },
        )
        return

    phase_map: dict[int, object] = {}
    for phase in profile.phases:  # type: ignore[union-attr]
        for idx in range(phase.message_range[0], phase.message_range[1]):
            phase_map[idx] = phase

    for event_idx, event in enumerate(profile.work_events):  # type: ignore[union-attr]
        phase = phase_map.get(event.start_index)
        if phase is not None and hasattr(phase, "start_time") and phase.start_time:
            signal_start = _as_local(phase.start_time)
        elif profile.first_message_at:  # type: ignore[union-attr]
            signal_start = _as_local(profile.first_message_at)  # type: ignore[union-attr]
        elif profile.created_at:  # type: ignore[union-attr]
            signal_start = _as_local(profile.created_at)  # type: ignore[union-attr]
        else:
            continue

        end_phase = phase_map.get(max(event.end_index - 1, event.start_index))
        if end_phase is not None and hasattr(end_phase, "end_time") and end_phase.end_time:
            signal_end = _as_local(end_phase.end_time)
        elif profile.last_message_at:  # type: ignore[union-attr]
            signal_end = _as_local(profile.last_message_at)  # type: ignore[union-attr]
        elif profile.updated_at:  # type: ignore[union-attr]
            signal_end = _as_local(profile.updated_at)  # type: ignore[union-attr]
        else:
            signal_end = signal_start + _TRANSCRIPT_SIGNAL_SECONDS

        if signal_end <= signal_start:
            signal_end = signal_start + _TRANSCRIPT_SIGNAL_SECONDS

        mode_hint = _WORK_EVENT_MODE_MAP.get(event.kind.value if hasattr(event.kind, "value") else str(event.kind), "chat")
        project_hint = (
            profile.canonical_projects[0]  # type: ignore[union-attr]
            if profile.canonical_projects  # type: ignore[union-attr]
            else _project_hint_from_paths(*event.file_paths, *profile.repo_paths)  # type: ignore[union-attr]
        )

        yield ActivitySignal(
            signal_id=_signal_id(
                "polylogue.session",
                signal_start,
                signal_end,
                profile.provider,  # type: ignore[union-attr]
                profile.conversation_id,  # type: ignore[union-attr]
                str(event_idx),
                event.kind.value if hasattr(event.kind, "value") else str(event.kind),
            ),
            source="polylogue.session",
            kind="session",
            start=signal_start,
            end=signal_end,
            mode_hint=mode_hint,
            project_hint=project_hint,
            title=profile.title,  # type: ignore[union-attr]
            detail=event.summary[:120] if event.summary else profile.conversation_id[:12],  # type: ignore[union-attr]
            evidence={
                "provider": profile.provider,  # type: ignore[union-attr]
                "conversation_id": profile.conversation_id,  # type: ignore[union-attr]
                "thread_id": profile.thread_id,  # type: ignore[union-attr]
                "work_event_kind": event.kind.value if hasattr(event.kind, "value") else str(event.kind),
                "work_event_confidence": event.confidence,
                "tool_categories": profile.tool_categories,  # type: ignore[union-attr]
                "file_paths": list(event.file_paths)[:10],
                "message_count": profile.message_count,  # type: ignore[union-attr]
                "total_cost_usd": profile.total_cost_usd,  # type: ignore[union-attr]
            },
        )


def _polylogue_session_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    seen_conv_ids: set[str] = set()
    # Fast path: pre-computed JSONL artefact (ms instead of 20+ seconds)
    yield from _iter_polylogue_signals_from_jsonl(_POLYLOGUE_ARTEFACT, start, end, seen_conv_ids=seen_conv_ids)
    # Live path: only conversations NOT already in the artefact.
    # Skip entirely if artefact is fresh (<2h) -- polylogue DB connection costs ~0.44s
    # even when it returns zero new conversations.
    cutover = _artefact_cutover(_POLYLOGUE_ARTEFACT)
    if cutover is None:
        yield from _polylogue_live_signals(start, end, seen_conv_ids)
        return
    now_utc = datetime.now(timezone.utc)
    if now_utc - cutover < timedelta(hours=2):
        return
    live_start = max(start, cutover - timedelta(days=7))
    yield from _polylogue_live_signals(live_start, end, seen_conv_ids)


def _polylogue_live_signals(
    start: datetime,
    end: datetime,
    seen_conv_ids: set[str],
) -> Iterator[ActivitySignal]:
    """Fetch only conversations NOT in seen_conv_ids from the polylogue DB."""
    try:
        from polylogue.lib.session_profile import build_session_profile
        from polylogue.sync import SyncPolylogue, _run
    except ImportError:
        return

    try:
        poly = SyncPolylogue()
    except Exception:
        return

    try:
        filt = poly.filter().since(start.isoformat()).until(end.isoformat())
        summaries = _run(filt.list_summaries())
        # Filter at summary level -- avoids fetching full conversation blobs for known IDs
        new_ids = [str(s.id) for s in summaries if str(s.id) not in seen_conv_ids]
        if not new_ids:
            return
        batch_size = 20
        for i in range(0, len(new_ids), batch_size):
            conversations = poly.get_conversations(new_ids[i : i + batch_size])
            for conv in conversations:
                try:
                    profile = build_session_profile(conv)
                except Exception:
                    continue
                yield from _profile_to_signals(profile)
    finally:
        poly.close()


def _numstat_record_to_signal(record: dict[str, object]) -> Optional[ActivitySignal]:
    """Convert a single iter_numstat record to an ActivitySignal, or None if invalid."""
    stamp = record.get("date")
    if not isinstance(stamp, str):
        return None
    try:
        signal_start = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    signal_start = _as_local(signal_start)
    signal_end = signal_start + _POINT_SIGNAL_SECONDS
    repo_path = _text(record.get("repo"))
    project_hint = _project_hint_from_paths(repo_path)
    if not project_hint and repo_path:
        project_hint = Path(repo_path).name
    return ActivitySignal(
        signal_id=_signal_id("git.commit", signal_start, signal_end, repo_path, record.get("commit")),
        source="git.commit",
        kind="git_commit",
        start=signal_start,
        end=signal_end,
        mode_hint="coding",
        project_hint=project_hint,
        detail=_text(record.get("subject")),
        evidence={
            "repo": repo_path,
            "commit": _text(record.get("commit")),
            "lines_added": record.get("lines_added"),
            "lines_deleted": record.get("lines_deleted"),
        },
    )


def _git_commit_signals(start: datetime, end: datetime) -> Iterator[ActivitySignal]:
    seen_shas: set[str] = set()
    # Fast path: pre-computed JSONL artefact (ms instead of 3-5s git subprocesses)
    yield from _iter_git_signals_from_jsonl(_GIT_ARTEFACT, start, end, seen_shas=seen_shas)
    # Live path: commits newer than the artefact cutover.
    # Skip if artefact is fresh (<1h) -- git subprocess overhead costs ~0.15s per run.
    cutover = _artefact_cutover(_GIT_ARTEFACT)
    if cutover is None:
        for record in gitstats.iter_numstat(gitstats.active_repo_paths(), since=start, until=end):
            sig = _numstat_record_to_signal(record)
            if sig is not None:
                yield sig
        return
    now_utc = datetime.now(timezone.utc)
    if now_utc - cutover < timedelta(hours=1):
        return
    live_since = max(start, cutover - timedelta(days=2))
    for record in gitstats.iter_numstat(gitstats.active_repo_paths(), since=live_since, until=end):
        sha = _text(record.get("commit"))
        if sha and sha in seen_shas:
            continue
        sig = _numstat_record_to_signal(record)
        if sig is not None:
            yield sig
