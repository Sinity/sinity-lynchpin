from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence
from urllib.parse import urlparse

from ..core.projects import ALL_PROJECTS
from ..sources.captures import activitywatch, atuin, instrumentation
from ..sources.exports import chatlog
from ..sources.indices import gitstats

DEFAULT_LOOKBACK_DAYS = 14
_AW_SAMPLE_CAP = timedelta(seconds=15)
_AW_SAMPLE_TAIL = timedelta(seconds=5)
_AW_COLLAPSE_GAP = timedelta(seconds=30)
_POINT_SIGNAL_SECONDS = timedelta(seconds=30)
_TRANSCRIPT_SIGNAL_SECONDS = timedelta(minutes=5)
_TERMINAL_COMMAND_SECONDS = timedelta(seconds=5)


@dataclass(frozen=True)
class TrajectorySignal:
    signal_id: str
    source: str
    kind: str
    start: datetime
    end: datetime
    mode_hint: Optional[str] = None
    project_hint: Optional[str] = None
    app: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    domain: Optional[str] = None
    cwd: Optional[str] = None
    detail: Optional[str] = None
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id,
            "source": self.source,
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "mode_hint": self.mode_hint,
            "project_hint": self.project_hint,
            "app": self.app,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "cwd": self.cwd,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def resolve_window(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    tz = _local_tz()
    end_dt = _as_local(end or now or datetime.now(tz))
    start_dt = _as_local(start) if start else end_dt - timedelta(days=days)
    if start_dt >= end_dt:
        raise ValueError(f"Invalid trajectory window: {start_dt.isoformat()} >= {end_dt.isoformat()}")
    return start_dt, end_dt


def load_signals(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[TrajectorySignal]:
    window_start, window_end = resolve_window(start=start, end=end, days=days)
    signals = list(_iter_all_signals(window_start, window_end))
    signals.sort(key=lambda signal: (signal.start, signal.end, signal.source, signal.signal_id))
    return signals


def iter_signals(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> Iterator[TrajectorySignal]:
    yield from load_signals(start=start, end=end, days=days)


def _iter_all_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    yield from _window_signals(start, end)
    yield from _web_signals(start, end)
    yield from _afk_signals(start, end)
    yield from _atuin_signals(start, end)
    yield from _terminal_session_signals(start, end)
    yield from _terminal_command_signals(start, end)
    yield from _chat_transcript_signals(start, end)
    yield from _git_commit_signals(start, end)


def _window_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    events = list(activitywatch.window_events(start=start, end=end))
    yield from _collapse_window_like(
        source="activitywatch.window",
        kind="window",
        events=events,
        app_key="app",
        title_key="title",
        url_key=None,
    )


def _web_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    events = list(activitywatch.web_events(start=start, end=end))
    yield from _collapse_window_like(
        source="activitywatch.web",
        kind="web",
        events=events,
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
) -> Iterator[TrajectorySignal]:
    current: Optional[TrajectorySignal] = None
    sample_count = 0

    for index, event in enumerate(events):
        start = _as_local(event.start)
        raw_end = _as_local(event.end)
        next_start = _as_local(events[index + 1].start) if index + 1 < len(events) else None
        effective_end = raw_end if raw_end > start else None
        if effective_end is None:
            if next_start and next_start > start:
                effective_end = min(next_start, start + _AW_SAMPLE_CAP)
            else:
                effective_end = start + _AW_SAMPLE_TAIL
        payload = event.data or {}
        app = _text(payload.get(app_key)) if app_key else None
        title = _text(payload.get(title_key)) if title_key else None
        url = _text(payload.get(url_key)) if url_key else None
        domain = _domain_from_url(url)
        project_hint = _project_hint_from_paths(
            _path_from_window_title(title),
            _project_hint_from_text(title),
            _project_hint_from_text(url),
        )

        candidate = TrajectorySignal(
            signal_id=_signal_id(source, start, effective_end, app, title, url),
            source=source,
            kind=kind,
            start=start,
            end=effective_end,
            app=app,
            title=title,
            url=url,
            domain=domain,
            project_hint=project_hint,
            evidence={"sample_count": 1, "bucket": event.bucket},
        )

        if (
            current
            and current.app == candidate.app
            and current.title == candidate.title
            and current.url == candidate.url
            and candidate.start <= current.end + _AW_COLLAPSE_GAP
        ):
            sample_count += 1
            current = TrajectorySignal(
                signal_id=current.signal_id,
                source=current.source,
                kind=current.kind,
                start=current.start,
                end=max(current.end, candidate.end),
                app=current.app,
                title=current.title,
                url=current.url,
                domain=current.domain,
                project_hint=current.project_hint or candidate.project_hint,
                evidence={**current.evidence, "sample_count": sample_count},
            )
            continue

        if current:
            yield current
        current = candidate
        sample_count = 1

    if current:
        yield current


def _afk_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for event in activitywatch.afk_events(start=start, end=end):
        status = _text((event.data or {}).get("status"))
        if status != "afk":
            continue
        signal_start = _as_local(event.start)
        signal_end = max(_as_local(event.end), signal_start)
        yield TrajectorySignal(
            signal_id=_signal_id("activitywatch.afk", signal_start, signal_end, status),
            source="activitywatch.afk",
            kind="afk",
            start=signal_start,
            end=signal_end,
            mode_hint="recovery",
            detail=status,
            evidence={"bucket": event.bucket, "status": status},
        )


def _atuin_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for command in atuin.iter_commands(start=start, end=end):
        signal_start = _as_local(command.timestamp)
        duration_seconds = 1.0
        if command.duration_ns and command.duration_ns > 0:
            duration_seconds = max(min(command.duration_ns / 1_000_000_000, 900.0), 1.0)
        signal_end = signal_start + timedelta(seconds=duration_seconds)
        yield TrajectorySignal(
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


def _terminal_session_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for session in instrumentation.iter_terminal_sessions():
        session_start = _parse_optional_dt(session.created_at)
        if session_start is None:
            continue
        session_end = _parse_optional_dt(session.finished_at)
        if session_end is None or session_end < session_start:
            duration_seconds = max(session.duration_seconds or 0.0, 0.0)
            session_end = session_start + timedelta(seconds=duration_seconds)
        session_start = _as_local(session_start)
        session_end = _as_local(session_end)
        if session_end <= start or session_start >= end:
            continue
        yield TrajectorySignal(
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


def _terminal_command_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for event in instrumentation.iter_terminal_session_events():
        if event.type != "command_start":
            continue
        signal_start = _parse_optional_dt(event.time)
        if signal_start is None:
            continue
        signal_start = _as_local(signal_start)
        if signal_start < start or signal_start >= end:
            continue
        signal_end = signal_start + _TERMINAL_COMMAND_SECONDS
        command_text = _text(event.payload.get("command") or event.payload.get("cmd"))
        yield TrajectorySignal(
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


def _chat_transcript_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for transcript in chatlog.iter_transcripts(start=start, end=end):
        signal_start = _as_local(transcript.started_at)
        signal_end = signal_start + _TRANSCRIPT_SIGNAL_SECONDS
        yield TrajectorySignal(
            signal_id=_signal_id("chatlog.transcript", signal_start, signal_end, transcript.provider, transcript.slug),
            source="chatlog.transcript",
            kind="transcript",
            start=signal_start,
            end=signal_end,
            mode_hint="chat",
            project_hint=_project_hint_from_paths(
                _project_hint_from_text(transcript.title),
                _project_hint_from_text(str(transcript.path)),
            ),
            title=transcript.title,
            detail=transcript.slug,
            evidence={
                "provider": transcript.provider,
                "path": str(transcript.path),
                "tokens": transcript.tokens,
                "words": transcript.words,
            },
        )


def _git_commit_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    for record in gitstats.iter_numstat(gitstats.active_repo_paths(), since=start, until=end):
        stamp = record.get("date")
        if not isinstance(stamp, str):
            continue
        try:
            signal_start = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        signal_start = _as_local(signal_start)
        signal_end = signal_start + _POINT_SIGNAL_SECONDS
        repo_path = _text(record.get("repo"))
        project_hint = _project_hint_from_paths(repo_path)
        if not project_hint and repo_path:
            project_hint = Path(repo_path).name
        yield TrajectorySignal(
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


def _parse_optional_dt(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_local(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_local(datetime.fromisoformat(text))
    except ValueError:
        return None


def _signal_id(source: str, start: datetime, end: datetime, *parts: object) -> str:
    payload = "|".join(
        [
            source,
            start.isoformat(),
            end.isoformat(),
            *[str(part or "") for part in parts],
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    domain = parsed.netloc.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def _path_from_window_title(title: Optional[str]) -> Optional[str]:
    if not title or "/realm/project/" not in title:
        return None
    marker = "/realm/project/"
    _, suffix = title.split(marker, 1)
    candidate = marker + suffix.split()[0]
    return candidate.rstrip(",:)")


def _project_hint_from_paths(*values: object) -> Optional[str]:
    for value in values:
        text = _text(value)
        if not text:
            continue
        try:
            path = Path(text).expanduser().resolve(strict=False)
        except OSError:
            continue
        for entry in ALL_PROJECTS.values():
            project_path = Path(entry.path).expanduser().resolve(strict=False)
            if path == project_path or project_path in path.parents:
                return entry.name
    return None


def _project_hint_from_text(value: object) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    lowered = text.lower()
    for name in sorted(ALL_PROJECTS, key=len, reverse=True):
        if name.lower() in lowered:
            return name
    return None


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_local_tz())
    return value.astimezone(_local_tz())


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc
