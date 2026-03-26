from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config

_REALM_PROJECT_ROOT = Path("/realm/project")
ACTIVE_GAP_SECONDS = 2.0
FULL_CAST_TIMING_SCAN_BYTES = 16 * 1024 * 1024
TAIL_CHUNK_BYTES = 256 * 1024
_CACHE_LOGGER = logging.getLogger(__name__ + ".cachew")
if _CACHE_LOGGER.level == logging.NOTSET:
    _CACHE_LOGGER.setLevel(logging.WARNING)


@dataclass
class TerminalSessionMetadata:
    """Normalized terminal-session metadata derived from cast + sidecars."""

    session_id: str
    path: str
    manifest_path: Optional[str]
    events_path: Optional[str]
    size_bytes: int
    created_at: Optional[str]
    finished_at: Optional[str]
    duration_seconds: Optional[float]
    active_seconds: Optional[float]
    idle_seconds: Optional[float]
    command_count: Optional[int]
    event_count: Optional[int]
    command: Optional[str]
    title: Optional[str]
    shell: Optional[str]
    term: Optional[str]
    term_type: Optional[str]
    term_cols: Optional[int]
    term_rows: Optional[int]
    host: Optional[str]
    user: Optional[str]
    tty: Optional[str]
    terminal: Optional[str]
    start_cwd: Optional[str]
    final_cwd: Optional[str]
    project_root: Optional[str]
    final_project_root: Optional[str]
    repo_root: Optional[str]
    final_repo_root: Optional[str]
    repo_branch: Optional[str]
    final_repo_branch: Optional[str]
    repo_commit: Optional[str]
    final_repo_commit: Optional[str]
    repo_dirty: Optional[bool]
    final_repo_dirty: Optional[bool]
    exit_code: Optional[int]
    exit_reason: Optional[str]
    recorder_exit_code: Optional[int]
    cleanup_escalated: Optional[bool]
    has_events: bool
    timing_source: Optional[str]
    schema_generation: str
    quality_status: str
    quality_flags: list[str] = field(default_factory=list)
    field_sources: dict[str, str] = field(default_factory=dict)


@dataclass
class TerminalSessionEvent:
    """Low-frequency terminal session events from events.jsonl."""

    session_id: str
    cast_path: str
    schema_generation: str
    source: str
    time: Optional[str]
    type: str
    pwd: Optional[str]
    project_root: Optional[str]
    repo_root: Optional[str]
    repo_branch: Optional[str]
    repo_commit: Optional[str]
    repo_dirty: Optional[bool]
    exit_code: Optional[int]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TerminalAuditEntry:
    path: str
    session_id: str
    schema_generation: str
    readable_header: bool
    header_version: Optional[int]
    has_manifest: bool
    has_events: bool
    has_command: bool
    has_geometry: bool
    has_activity_estimate: bool
    duration_seconds: Optional[float]
    timing_source: Optional[str]
    status: str
    issues: list[str] = field(default_factory=list)


@dataclass
class TerminalAuditSummary:
    cast_count: int = 0
    manifest_count: int = 0
    events_count: int = 0
    unreadable_header_count: int = 0
    counts_by_generation: dict[str, int] = field(default_factory=dict)
    counts_by_header_version: dict[str, int] = field(default_factory=dict)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    counts_by_timing_source: dict[str, int] = field(default_factory=dict)
    missing_manifest_count: int = 0
    missing_events_count: int = 0
    sessions_with_command_count: int = 0
    sessions_with_geometry_count: int = 0
    missing_activity_estimate_count: int = 0
    header_only_count: int = 0
    degraded_count: int = 0
    damaged_count: int = 0
    quarantine_candidate_count: int = 0
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None


@dataclass
class AudioMetadata:
    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    duration_seconds: Optional[float]
    format: Optional[str]
    channels: Optional[int]
    sample_rate: Optional[int]


@dataclass
class ScreenMetadata:
    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    width: Optional[int]
    height: Optional[int]
    format: Optional[str]


@dataclass
class _CastHeaderSummary:
    header_json: str
    duration_seconds: float
    active_seconds: Optional[float]
    idle_seconds: Optional[float]
    timing_source: str


@dataclass
class _CastTimingSummary:
    duration_seconds: float
    active_seconds: Optional[float]
    idle_seconds: Optional[float]


def iter_terminal_sessions(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionMetadata]:
    """Scan for terminal session casts and yield normalized session metadata.

    When *start* and *end* are provided, only cast files stored in YYYY/MM/DD
    directories that overlap the window are scanned, giving a large speedup for
    short windows over a large corpus.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionMetadata]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            meta = _parse_terminal_session(cast_path)
            if meta:
                yield meta

    return generator()


def terminal_sessions_by_date(target: date, root: Path | None = None) -> Iterator[TerminalSessionMetadata]:
    """Yield terminal sessions that overlap the requested date."""

    start, end = _local_day_bounds(target)

    def generator() -> Iterator[TerminalSessionMetadata]:
        for session in iter_terminal_sessions(root):
            created = _parse_iso_datetime(session.created_at)
            finished = _parse_iso_datetime(session.finished_at) or created
            if created is None:
                continue
            if finished is None:
                finished = created
            if created < end and finished >= start:
                yield session

    return generator()


def terminal_session_events_by_date(target: date, root: Path | None = None) -> Iterator[TerminalSessionEvent]:
    """Yield terminal session events that fall within the requested local day."""

    start, end = _local_day_bounds(target)

    def generator() -> Iterator[TerminalSessionEvent]:
        for event in iter_terminal_session_events(root):
            event_time = _parse_iso_datetime(event.time) or _parse_iso_datetime(_session_time_from_id(event.session_id))
            if event_time is None:
                continue
            if start <= event_time < end:
                yield event

    return generator()


def iter_terminal_session_events(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionEvent]:
    """Yield low-frequency terminal session events.

    When *start* and *end* are provided, only cast files stored in YYYY/MM/DD
    directories that overlap the window are scanned.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionEvent]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            yield from _parse_terminal_session_events(cast_path)

    return generator()


def iter_terminal_audit(root: Path | None = None) -> Iterator[TerminalAuditEntry]:
    """Yield per-session audit entries for the terminal corpus."""

    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalAuditEntry]:
        for cast_path in _iter_cast_paths(scan_root):
            entry = _audit_terminal_session(cast_path)
            if entry:
                yield entry

    return generator()


def iter_terminal_sessions_fast(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionMetadata]:
    """Fast variant of iter_terminal_sessions that reads only the manifest JSON sidecar.

    Skips the expensive cast-file timing scan (which can read megabytes of cast data).
    All fields needed by the activity-signal pipeline are present in the manifest;
    sessions without a manifest fall back to a minimal parse of the cast header.
    Use this in hot paths where timing accuracy is less critical than speed.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionMetadata]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            meta = _parse_session_fast(cast_path)
            if meta:
                yield meta

    return generator()


def _parse_session_fast(cast_path: Path) -> Optional[TerminalSessionMetadata]:
    """Parse only the manifest sidecar (session.json), with a minimal cast-header fallback.

    This avoids _scan_cast_timings on the (potentially large) cast file.
    Suitable for the activity-signal pipeline where timing precision is secondary.
    """
    manifest_path, events_path = _sidecar_paths(cast_path)
    session_id = _session_id(cast_path)

    manifest = _load_json_file(manifest_path)
    if manifest is None:
        # No manifest — fall back to reading just the cast header line (cheap)
        try:
            with cast_path.open("r", encoding="utf-8", errors="ignore") as fh:
                header_line = fh.readline()
            if not header_line:
                return None
            header = json.loads(header_line)
        except (OSError, json.JSONDecodeError):
            return None
        start_ts = _to_float(header.get("timestamp"))
        created_at = _local_iso_from_epoch_seconds(start_ts)
        if not created_at:
            return None
        schema_gen = _schema_generation(None, header)
        env = header.get("env") or {}
        return TerminalSessionMetadata(
            session_id=session_id,
            path=str(cast_path),
            manifest_path=None,
            events_path=str(events_path) if events_path.exists() else None,
            size_bytes=cast_path.stat().st_size,
            created_at=created_at,
            finished_at=None,
            duration_seconds=None,
            active_seconds=None,
            idle_seconds=None,
            command_count=None,
            event_count=None,
            command=_to_text(header.get("command")),
            title=_to_text(header.get("title")),
            shell=_to_text(env.get("SHELL")),
            term=_to_text(env.get("TERM")),
            term_type=None,
            term_cols=None,
            term_rows=None,
            host=_to_text(env.get("SINNIX_CAPTURE_HOST") or env.get("HOSTNAME")),
            user=_to_text(env.get("SINNIX_CAPTURE_USER")),
            tty=_to_text(env.get("SINNIX_CAPTURE_TTY")),
            terminal=_to_text(env.get("SINNIX_CAPTURE_TERMINAL")),
            start_cwd=_to_text(env.get("SINNIX_CAPTURE_START_CWD")),
            final_cwd=None,
            project_root=_to_text(env.get("SINNIX_CAPTURE_PROJECT_ROOT")),
            final_project_root=None,
            repo_root=None,
            final_repo_root=None,
            repo_branch=None,
            final_repo_branch=None,
            repo_commit=None,
            final_repo_commit=None,
            repo_dirty=None,
            final_repo_dirty=None,
            exit_code=None,
            exit_reason=None,
            recorder_exit_code=None,
            cleanup_escalated=None,
            has_events=events_path.exists(),
            timing_source="header_only",
            schema_generation=schema_gen,
            quality_status="unknown",
        )

    # Manifest path — read all fields from the JSON (fast, small file)
    schema_gen = _schema_generation(manifest, None)
    has_events = bool(manifest.get("has_events")) or events_path.exists()

    return TerminalSessionMetadata(
        session_id=_to_text(manifest.get("session_id")) or session_id,
        path=str(cast_path),
        manifest_path=str(manifest_path),
        events_path=str(events_path) if events_path.exists() else None,
        size_bytes=cast_path.stat().st_size,
        created_at=_manifest_time(manifest, "started_at", "started_at_ms"),
        finished_at=_manifest_time(manifest, "finished_at", "finished_at_ms"),
        duration_seconds=_ms_to_seconds(manifest.get("duration_ms")),
        active_seconds=_ms_to_seconds(manifest.get("active_ms")),
        idle_seconds=_ms_to_seconds(manifest.get("idle_ms")),
        command_count=_to_int(manifest.get("command_count")),
        event_count=_to_int(manifest.get("event_count")),
        command=_to_text(manifest.get("command")),
        title=_to_text(manifest.get("title")),
        shell=_to_text(manifest.get("shell")),
        term=None,
        term_type=None,
        term_cols=None,
        term_rows=None,
        host=_to_text(manifest.get("host")),
        user=_to_text(manifest.get("user")),
        tty=_to_text(manifest.get("tty")),
        terminal=_to_text(manifest.get("terminal")),
        start_cwd=_to_text(manifest.get("start_cwd")),
        final_cwd=_to_text(manifest.get("final_cwd")),
        project_root=_to_text(manifest.get("project_root")),
        final_project_root=_to_text(manifest.get("final_project_root")),
        repo_root=_to_text(manifest.get("repo_root") or manifest.get("start_repo_root")),
        final_repo_root=_to_text(manifest.get("final_repo_root")),
        repo_branch=_to_text(manifest.get("repo_branch")),
        final_repo_branch=_to_text(manifest.get("final_repo_branch")),
        repo_commit=_to_text(manifest.get("repo_commit")),
        final_repo_commit=_to_text(manifest.get("final_repo_commit")),
        repo_dirty=_to_bool(manifest.get("repo_dirty")),
        final_repo_dirty=_to_bool(manifest.get("final_repo_dirty")),
        exit_code=_to_int(manifest.get("exit_code")),
        exit_reason=_to_text(manifest.get("exit_reason")),
        recorder_exit_code=_to_int(manifest.get("recorder_exit_code")),
        cleanup_escalated=_to_bool(manifest.get("cleanup_escalated")),
        has_events=has_events,
        timing_source="manifest_fast",
        schema_generation=schema_gen,
        quality_status=_to_text(manifest.get("quality_status")) or "unknown",
        quality_flags=manifest.get("quality_flags") or [],
        field_sources={},
    )


def summarize_terminal_audit(entries: Iterator[TerminalAuditEntry]) -> TerminalAuditSummary:
    summary = TerminalAuditSummary()
    for entry in entries:
        summary.cast_count += 1
        if not entry.readable_header:
            summary.unreadable_header_count += 1
        if entry.has_manifest:
            summary.manifest_count += 1
        else:
            summary.missing_manifest_count += 1
        if entry.has_events:
            summary.events_count += 1
        else:
            summary.missing_events_count += 1
        if entry.has_command:
            summary.sessions_with_command_count += 1
        if entry.has_geometry:
            summary.sessions_with_geometry_count += 1
        if not entry.has_activity_estimate:
            summary.missing_activity_estimate_count += 1
        summary.counts_by_generation[entry.schema_generation] = (
            summary.counts_by_generation.get(entry.schema_generation, 0) + 1
        )
        summary.counts_by_status[entry.status] = summary.counts_by_status.get(entry.status, 0) + 1
        timing_key = entry.timing_source or "unknown"
        summary.counts_by_timing_source[timing_key] = summary.counts_by_timing_source.get(timing_key, 0) + 1
        version_key = "unknown" if entry.header_version is None else str(entry.header_version)
        summary.counts_by_header_version[version_key] = (
            summary.counts_by_header_version.get(version_key, 0) + 1
        )
        if entry.status == "header-only":
            summary.header_only_count += 1
        elif entry.status == "degraded":
            summary.degraded_count += 1
        elif entry.status == "damaged":
            summary.damaged_count += 1
            summary.quarantine_candidate_count += 1

        session_time = _session_time_from_id(entry.session_id)
        if session_time:
            if summary.date_range_start is None or session_time < summary.date_range_start:
                summary.date_range_start = session_time
            if summary.date_range_end is None or session_time > summary.date_range_end:
                summary.date_range_end = session_time

    return summary


def iter_audio_recordings(root: Path | None = None) -> Iterator[AudioMetadata]:
    cfg = get_config()
    scan_root = Path(root) if root else cfg.audio_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[AudioMetadata]:
        for ext in ("*.wav", "*.mp3", "*.flac", "*.opus", "*.m4a", "*.aac"):
            for path in scan_root.rglob(ext):
                if not path.is_file():
                    continue
                meta = _parse_audio(path)
                if meta:
                    yield meta

    return generator()


def iter_screenshots(root: Path | None = None) -> Iterator[ScreenMetadata]:
    cfg = get_config()
    scan_root = Path(root) if root else cfg.screenshot_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[ScreenMetadata]:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4", "*.webm", "*.mkv"):
            for path in scan_root.rglob(ext):
                if not path.is_file():
                    continue
                meta = _parse_screen(path)
                if meta:
                    yield meta

    return generator()


def _iter_cast_paths(scan_root: Path) -> Iterator[Path]:
    for cast_path in sorted(scan_root.rglob("session.cast")):
        if cast_path.is_file():
            yield cast_path


def _iter_cast_paths_for_window(
    scan_root: Path,
    start: datetime,
    end: datetime,
) -> Iterator[Path]:
    """Yield cast paths only within YYYY/MM/DD directories that overlap [start, end].

    The asciinema tree is organised as scan_root/YYYY/MM/DD/session-dir/session.cast.
    Walking only the relevant day directories avoids stat()-ing the entire corpus
    (typically 1 000+ files) for short time windows.
    """
    # Convert to local dates; extend by 1 day on each side to cover sessions
    # that straddle midnight or span multiple days.
    start_date = (start.astimezone().replace(tzinfo=None) - timedelta(days=1)).date()
    end_date = (end.astimezone().replace(tzinfo=None) + timedelta(days=1)).date()

    try:
        year_dirs = sorted(scan_root.iterdir())
    except OSError:
        return

    for year_dir in year_dirs:
        if not year_dir.is_dir():
            continue
        try:
            year = int(year_dir.name)
        except ValueError:
            continue
        if year < start_date.year or year > end_date.year:
            continue

        try:
            month_dirs = sorted(year_dir.iterdir())
        except OSError:
            continue

        for month_dir in month_dirs:
            if not month_dir.is_dir():
                continue
            try:
                month = int(month_dir.name)
            except ValueError:
                continue
            if (year, month) < (start_date.year, start_date.month):
                continue
            if (year, month) > (end_date.year, end_date.month):
                continue

            try:
                day_dirs = sorted(month_dir.iterdir())
            except OSError:
                continue

            for day_dir in day_dirs:
                if not day_dir.is_dir():
                    continue
                try:
                    day = int(day_dir.name)
                    d = date(year, month, day)
                except (ValueError, TypeError):
                    continue
                if d < start_date or d > end_date:
                    continue

                for cast_path in sorted(day_dir.rglob("session.cast")):
                    if cast_path.is_file():
                        yield cast_path


def _parse_terminal_session(cast_path: Path) -> Optional[TerminalSessionMetadata]:
    header, duration_seconds, active_seconds, idle_seconds, timing_source = _read_cast_header(cast_path)
    if header is None:
        return None

    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    event_summary = (
        _summarize_session_events(_parse_terminal_session_events(cast_path))
        if events_path.exists()
        else _SessionEventSummary()
    )

    field_sources: dict[str, str] = {}
    values: dict[str, Any] = {
        "session_id": session_id,
        "path": str(cast_path),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "events_path": str(events_path) if events_path.exists() else None,
        "size_bytes": cast_path.stat().st_size,
        "created_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "active_seconds": None,
        "idle_seconds": None,
        "command_count": None,
        "event_count": None,
        "command": None,
        "title": None,
        "shell": None,
        "term": None,
        "term_type": None,
        "term_cols": None,
        "term_rows": None,
        "host": None,
        "user": None,
        "tty": None,
        "terminal": None,
        "start_cwd": None,
        "final_cwd": None,
        "project_root": None,
        "final_project_root": None,
        "repo_root": None,
        "final_repo_root": None,
        "repo_branch": None,
        "final_repo_branch": None,
        "repo_commit": None,
        "final_repo_commit": None,
        "repo_dirty": None,
        "final_repo_dirty": None,
        "exit_code": None,
        "exit_reason": None,
        "recorder_exit_code": None,
        "cleanup_escalated": None,
    }

    def assign(field: str, value: Any, source: str) -> None:
        if _is_blank(value):
            return
        if values.get(field) is None:
            values[field] = value
            field_sources[field] = source

    if manifest:
        assign("session_id", manifest.get("session_id"), "manifest")
        assign("created_at", _manifest_time(manifest, "started_at", "started_at_ms"), "manifest")
        assign("finished_at", _manifest_time(manifest, "finished_at", "finished_at_ms"), "manifest")
        assign("duration_seconds", _ms_to_seconds(manifest.get("duration_ms")), "manifest")
        assign("active_seconds", _ms_to_seconds(manifest.get("active_ms")), "manifest")
        assign("idle_seconds", _ms_to_seconds(manifest.get("idle_ms")), "manifest")
        assign("command_count", _to_int(manifest.get("command_count")), "manifest")
        assign("event_count", _to_int(manifest.get("event_count")), "manifest")
        assign("command", _to_text(manifest.get("command")), "manifest")
        assign("title", _to_text(manifest.get("title")), "manifest")
        assign("shell", _to_text(manifest.get("shell")), "manifest")
        assign("host", _to_text(manifest.get("host")), "manifest")
        assign("user", _to_text(manifest.get("user")), "manifest")
        assign("tty", _to_text(manifest.get("tty")), "manifest")
        assign("terminal", _to_text(manifest.get("terminal")), "manifest")
        assign("start_cwd", _to_text(manifest.get("start_cwd")), "manifest")
        assign("final_cwd", _to_text(manifest.get("final_cwd")), "manifest")
        assign("project_root", _to_text(manifest.get("project_root")), "manifest")
        assign("final_project_root", _to_text(manifest.get("final_project_root")), "manifest")
        assign("repo_root", _to_text(manifest.get("repo_root") or manifest.get("start_repo_root")), "manifest")
        assign("final_repo_root", _to_text(manifest.get("final_repo_root")), "manifest")
        assign("repo_branch", _to_text(manifest.get("repo_branch")), "manifest")
        assign("final_repo_branch", _to_text(manifest.get("final_repo_branch")), "manifest")
        assign("repo_commit", _to_text(manifest.get("repo_commit")), "manifest")
        assign("final_repo_commit", _to_text(manifest.get("final_repo_commit")), "manifest")
        assign("repo_dirty", _to_bool(manifest.get("repo_dirty")), "manifest")
        assign("final_repo_dirty", _to_bool(manifest.get("final_repo_dirty")), "manifest")
        assign("exit_code", _to_int(manifest.get("exit_code")), "manifest")
        assign("exit_reason", _to_text(manifest.get("exit_reason")), "manifest")
        assign("recorder_exit_code", _to_int(manifest.get("recorder_exit_code")), "manifest")
        assign("cleanup_escalated", _to_bool(manifest.get("cleanup_escalated")), "manifest")

    start_ts = _to_float(header.get("timestamp"))
    created_at = _local_iso_from_epoch_seconds(start_ts)
    finished_at = _local_iso_from_epoch_seconds(start_ts + duration_seconds) if start_ts is not None else None
    env = header.get("env") or {}
    term = header.get("term") or {}

    assign("created_at", created_at, "cast_header")
    assign("finished_at", finished_at, "cast_header")
    assign("duration_seconds", duration_seconds, "cast_header")
    assign("active_seconds", active_seconds, "cast_header")
    assign("idle_seconds", idle_seconds, "cast_header")
    assign("command", _to_text(header.get("command")), "cast_header")
    assign("title", _to_text(header.get("title")), "cast_header")
    assign("shell", _to_text(env.get("SHELL")), "cast_header")
    assign("term", _to_text(env.get("TERM")), "cast_header")
    assign("term_type", _to_text(term.get("type")), "cast_header")
    assign("term_cols", _to_int(term.get("cols") or header.get("width")), "cast_header")
    assign("term_rows", _to_int(term.get("rows") or header.get("height")), "cast_header")
    assign("host", _to_text(env.get("SINNIX_CAPTURE_HOST") or env.get("HOSTNAME")), "cast_header")
    assign("user", _to_text(env.get("SINNIX_CAPTURE_USER")), "cast_header")
    assign("tty", _to_text(env.get("SINNIX_CAPTURE_TTY")), "cast_header")
    assign("terminal", _to_text(env.get("SINNIX_CAPTURE_TERMINAL")), "cast_header")
    assign("start_cwd", _to_text(env.get("SINNIX_CAPTURE_START_CWD")), "cast_header")
    assign("project_root", _to_text(env.get("SINNIX_CAPTURE_PROJECT_ROOT")), "cast_header")
    assign("repo_root", _to_text(env.get("SINNIX_CAPTURE_START_REPO_ROOT") or env.get("SINNIX_CAPTURE_REPO_ROOT")), "cast_header")
    assign("repo_branch", _to_text(env.get("SINNIX_CAPTURE_START_REPO_BRANCH")), "cast_header")
    assign("repo_commit", _to_text(env.get("SINNIX_CAPTURE_START_REPO_COMMIT")), "cast_header")
    assign("repo_dirty", _to_bool(env.get("SINNIX_CAPTURE_START_REPO_DIRTY")), "cast_header")
    assign("session_id", _to_text(env.get("SINNIX_CAPTURE_SESSION_ID")), "cast_header")

    assign("created_at", event_summary.started_at, "events")
    assign("finished_at", event_summary.finished_at, "events")
    assign("command_count", event_summary.command_count, "events")
    assign("event_count", event_summary.event_count, "events")
    assign("active_seconds", event_summary.active_seconds, "events")
    assign("idle_seconds", event_summary.idle_seconds, "events")
    assign("command", event_summary.first_command, "events")
    assign("start_cwd", event_summary.start_cwd, "events")
    assign("final_cwd", event_summary.final_cwd, "events")
    assign("project_root", event_summary.project_root, "events")
    assign("final_project_root", event_summary.final_project_root, "events")
    assign("repo_root", event_summary.repo_root, "events")
    assign("final_repo_root", event_summary.final_repo_root, "events")
    assign("repo_branch", event_summary.repo_branch, "events")
    assign("final_repo_branch", event_summary.final_repo_branch, "events")
    assign("repo_commit", event_summary.repo_commit, "events")
    assign("final_repo_commit", event_summary.final_repo_commit, "events")
    assign("repo_dirty", event_summary.repo_dirty, "events")
    assign("final_repo_dirty", event_summary.final_repo_dirty, "events")
    assign("exit_code", event_summary.exit_code, "events")

    if values["project_root"] is None:
        assign("project_root", _guess_project_root(values["start_cwd"] or values["repo_root"]), "derived")
    if values["final_project_root"] is None:
        assign("final_project_root", _guess_project_root(values["final_cwd"] or values["final_repo_root"]), "derived")
    if values["repo_root"] is None:
        assign("repo_root", _guess_project_root(values["start_cwd"]), "derived")
    if values["final_repo_root"] is None:
        assign("final_repo_root", _guess_project_root(values["final_cwd"]), "derived")

    schema_generation = _schema_generation(manifest, header)
    has_events = events_path.exists()

    if values["finished_at"] is None and values["created_at"] is not None and values["duration_seconds"] is not None:
        created_dt = _parse_iso_datetime(values["created_at"])
        if created_dt is not None:
            derived_finished = (created_dt + timedelta(seconds=float(values["duration_seconds"]))).isoformat()
            assign("finished_at", derived_finished, "derived")

    if values["duration_seconds"] is None:
        assign(
            "duration_seconds",
            _duration_between(values["created_at"], values["finished_at"]),
            "derived",
        )

    if values["active_seconds"] is None and timing_source in {"tail", "full-fallback"}:
        full_timing = _read_cast_full_timing(cast_path)
        if full_timing.active_seconds is not None:
            values["duration_seconds"] = full_timing.duration_seconds
            values["active_seconds"] = full_timing.active_seconds
            values["idle_seconds"] = full_timing.idle_seconds
            field_sources["duration_seconds"] = "cast_full_timing"
            field_sources["active_seconds"] = "cast_full_timing"
            field_sources["idle_seconds"] = "cast_full_timing"
            timing_source = "full-backfill"

    quality_status, quality_flags = _assess_session_quality(
        manifest_exists=manifest_path.exists(),
        has_events=has_events,
        schema_generation=schema_generation,
        created_at=values["created_at"],
        finished_at=values["finished_at"],
        duration_seconds=values["duration_seconds"],
        active_seconds=values["active_seconds"],
        command=values["command"],
        timing_source=timing_source,
    )

    return TerminalSessionMetadata(
        session_id=str(values["session_id"] or session_id),
        path=str(cast_path),
        manifest_path=values["manifest_path"],
        events_path=values["events_path"],
        size_bytes=values["size_bytes"],
        created_at=values["created_at"],
        finished_at=values["finished_at"],
        duration_seconds=values["duration_seconds"],
        active_seconds=values["active_seconds"],
        idle_seconds=values["idle_seconds"],
        command_count=values["command_count"],
        event_count=values["event_count"],
        command=values["command"],
        title=values["title"],
        shell=values["shell"],
        term=values["term"],
        term_type=values["term_type"],
        term_cols=values["term_cols"],
        term_rows=values["term_rows"],
        host=values["host"],
        user=values["user"],
        tty=values["tty"],
        terminal=values["terminal"],
        start_cwd=values["start_cwd"],
        final_cwd=values["final_cwd"],
        project_root=values["project_root"],
        final_project_root=values["final_project_root"],
        repo_root=values["repo_root"],
        final_repo_root=values["final_repo_root"],
        repo_branch=values["repo_branch"],
        final_repo_branch=values["final_repo_branch"],
        repo_commit=values["repo_commit"],
        final_repo_commit=values["final_repo_commit"],
        repo_dirty=values["repo_dirty"],
        final_repo_dirty=values["final_repo_dirty"],
        exit_code=values["exit_code"],
        exit_reason=values["exit_reason"],
        recorder_exit_code=values["recorder_exit_code"],
        cleanup_escalated=values["cleanup_escalated"],
        has_events=has_events,
        timing_source=timing_source,
        schema_generation=schema_generation,
        quality_status=quality_status,
        quality_flags=quality_flags,
        field_sources=field_sources,
    )


def _parse_terminal_session_events(cast_path: Path) -> Iterator[TerminalSessionEvent]:
    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    schema_generation = _schema_generation(manifest, None)

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    time = _to_text(payload.get("time")) or _local_iso_from_epoch_ms(payload.get("ts_ms"))
                    pwd = _to_text(payload.get("cwd") or payload.get("pwd"))
                    repo_root = _to_text(payload.get("repo_root"))
                    project_root = _to_text(payload.get("project_root")) or _guess_project_root(pwd or repo_root)

                    yield TerminalSessionEvent(
                        session_id=str(payload.get("session_id") or session_id),
                        cast_path=str(cast_path),
                        schema_generation=schema_generation,
                        source="events_jsonl",
                        time=time,
                        type=str(payload.get("type") or "unknown"),
                        pwd=pwd,
                        project_root=project_root,
                        repo_root=repo_root,
                        repo_branch=_to_text(payload.get("repo_branch")),
                        repo_commit=_to_text(payload.get("repo_commit")),
                        repo_dirty=_to_bool(payload.get("repo_dirty")),
                        exit_code=_to_int(
                            payload.get("exit_code")
                            if payload.get("exit_code") is not None
                            else payload.get("status")
                        ),
                        payload=payload,
                    )
        except OSError:
            return


def _audit_terminal_session(cast_path: Path) -> Optional[TerminalAuditEntry]:
    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    header, duration_seconds, _, _, timing_source = _read_cast_header(cast_path)
    if header is None:
        return TerminalAuditEntry(
            path=str(cast_path),
            session_id=session_id,
            schema_generation=_schema_generation(manifest, None),
            readable_header=False,
            header_version=None,
            has_manifest=manifest_path.exists(),
            has_events=events_path.exists(),
            has_command=False,
            has_geometry=False,
            has_activity_estimate=False,
            duration_seconds=None,
            timing_source=None,
            status="damaged",
            issues=["unreadable_header"],
        )

    session = _parse_terminal_session(cast_path)
    term = header.get("term") or {}

    return TerminalAuditEntry(
        path=str(cast_path),
        session_id=session_id,
        schema_generation=(session.schema_generation if session else _schema_generation(manifest, header)),
        readable_header=True,
        header_version=_to_int(header.get("version")),
        has_manifest=manifest_path.exists(),
        has_events=events_path.exists(),
        has_command=(session.command is not None) if session else (_to_text(header.get("command")) is not None),
        has_geometry=_to_int(term.get("cols") or header.get("width")) is not None and _to_int(term.get("rows") or header.get("height")) is not None,
        has_activity_estimate=session.active_seconds is not None if session else False,
        duration_seconds=session.duration_seconds if session else duration_seconds,
        timing_source=session.timing_source if session else timing_source,
        status=session.quality_status if session else "ok",
        issues=list(session.quality_flags) if session else [],
    )


@persistent_cache(
    "terminal_cast_summaries",
    depends_on=lambda path: file_signature(path),
    logger=_CACHE_LOGGER,
)
def _read_cast_summary(path: Path) -> Optional[_CastHeaderSummary]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            header_line = fh.readline()
            if not header_line:
                return None
    except OSError:
        return None

    try:
        header = json.loads(header_line)
    except json.JSONDecodeError:
        return None

    stat = path.stat()
    duration_seconds: float = 0.0
    active_seconds: Optional[float] = None
    idle_seconds: Optional[float] = None
    timing_source = "tail"

    if stat.st_size <= FULL_CAST_TIMING_SCAN_BYTES:
        duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
        timing_source = "full"
    else:
        last_time = _read_last_cast_timestamp(path)
        if last_time is None:
            duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
            timing_source = "full-fallback"
        else:
            duration_seconds = last_time

    return _CastHeaderSummary(
        header_json=json.dumps(header, ensure_ascii=False, sort_keys=True),
        duration_seconds=duration_seconds,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
        timing_source=timing_source,
    )


def _read_cast_header(path: Path) -> tuple[Optional[dict[str, Any]], float, Optional[float], Optional[float], Optional[str]]:
    summary = _read_cast_summary(path)
    if summary is None:
        return None, 0.0, None, None, None
    try:
        header = json.loads(summary.header_json)
    except json.JSONDecodeError:
        return None, 0.0, None, None, None
    return header, summary.duration_seconds, summary.active_seconds, summary.idle_seconds, summary.timing_source


@persistent_cache(
    "terminal_cast_full_timing",
    depends_on=lambda path: file_signature(path),
    logger=_CACHE_LOGGER,
)
def _read_cast_full_timing(path: Path) -> _CastTimingSummary:
    duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
    return _CastTimingSummary(
        duration_seconds=duration_seconds,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
    )


def _scan_cast_timings(path: Path) -> tuple[float, Optional[float], Optional[float]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            next(fh, None)
            duration_seconds = 0.0
            active_seconds = 0.0
            idle_seconds = 0.0
            previous_time = 0.0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, list) and event:
                    timestamp = _to_float(event[0])
                    if timestamp is None or timestamp < 0:
                        continue
                    delta = max(timestamp - previous_time, 0.0)
                    duration_seconds = max(duration_seconds, timestamp)
                    active_seconds += min(delta, ACTIVE_GAP_SECONDS)
                    idle_seconds += max(delta - ACTIVE_GAP_SECONDS, 0.0)
                    previous_time = max(previous_time, timestamp)
            return duration_seconds, active_seconds, idle_seconds
    except OSError:
        return 0.0, None, None


def _read_last_cast_timestamp(path: Path) -> Optional[float]:
    try:
        file_size = path.stat().st_size
        if file_size <= 0:
            return None

        with path.open("rb") as fh:
            buffer = b""
            offset = file_size
            while offset > 0:
                read_size = min(TAIL_CHUNK_BYTES, offset)
                offset -= read_size
                fh.seek(offset)
                buffer = fh.read(read_size) + buffer
                lines = [line.strip() for line in buffer.splitlines() if line.strip()]
                if offset > 0 and len(lines) < 2:
                    continue
                for raw in reversed(lines):
                    try:
                        event = json.loads(raw.decode("utf-8", errors="ignore"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, list) and event:
                        timestamp = _to_float(event[0])
                        if timestamp is not None and timestamp >= 0:
                            return timestamp
            return None
    except OSError:
        return None


@dataclass
class _SessionEventSummary:
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    first_event_time: Optional[str] = None
    last_event_time: Optional[str] = None
    start_cwd: Optional[str] = None
    final_cwd: Optional[str] = None
    project_root: Optional[str] = None
    final_project_root: Optional[str] = None
    repo_root: Optional[str] = None
    final_repo_root: Optional[str] = None
    repo_branch: Optional[str] = None
    final_repo_branch: Optional[str] = None
    repo_commit: Optional[str] = None
    final_repo_commit: Optional[str] = None
    repo_dirty: Optional[bool] = None
    final_repo_dirty: Optional[bool] = None
    command_count: Optional[int] = None
    event_count: Optional[int] = None
    first_command: Optional[str] = None
    exit_code: Optional[int] = None
    active_seconds: Optional[float] = None
    idle_seconds: Optional[float] = None


def _summarize_session_events(events: Iterator[TerminalSessionEvent]) -> _SessionEventSummary:
    summary = _SessionEventSummary(command_count=0, event_count=0)
    previous_event_dt: Optional[datetime] = None
    active_seconds = 0.0
    idle_seconds = 0.0
    saw_activity_time = False

    for event in events:
        summary.event_count = (summary.event_count or 0) + 1
        if event.time:
            summary.first_event_time = summary.first_event_time or event.time
            summary.last_event_time = event.time
        event_dt = _parse_iso_datetime(event.time)
        if event_dt is not None:
            saw_activity_time = True
            if previous_event_dt is not None:
                delta = max((event_dt - previous_event_dt).total_seconds(), 0.0)
                active_seconds += min(delta, ACTIVE_GAP_SECONDS)
                idle_seconds += max(delta - ACTIVE_GAP_SECONDS, 0.0)
            previous_event_dt = event_dt
        if event.type == "session_start":
            summary.started_at = summary.started_at or event.time
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.project_root = summary.project_root or event.project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
        elif event.type == "command_start":
            summary.command_count = (summary.command_count or 0) + 1
            command = _to_text(event.payload.get("command") or event.payload.get("cmd"))
            summary.first_command = summary.first_command or command
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.project_root = summary.project_root or event.project_root
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
        elif event.type in {"location", "cwd"}:
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.project_root = summary.project_root or event.project_root
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
        elif event.type in {"session_end", "shell_exit"}:
            summary.finished_at = event.time or summary.finished_at
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
            summary.exit_code = event.exit_code if event.exit_code is not None else summary.exit_code
        elif event.type == "command_end":
            summary.exit_code = event.exit_code if event.exit_code is not None else summary.exit_code

    if saw_activity_time:
        summary.active_seconds = active_seconds
        summary.idle_seconds = idle_seconds
    summary.started_at = summary.started_at or summary.first_event_time
    summary.finished_at = summary.finished_at or summary.last_event_time

    return summary


def _session_id(cast_path: Path) -> str:
    return cast_path.parent.name


def _sidecar_paths(cast_path: Path) -> tuple[Path, Path]:
    return (
        cast_path.with_name("session.json"),
        cast_path.with_name("events.jsonl"),
    )


def _schema_generation(
    manifest: Optional[dict[str, Any]],
    header: Optional[dict[str, Any]],
) -> str:
    if manifest:
        return str(manifest.get("schema_generation") or manifest.get("schema") or "terminal-session-v1")
    version = _to_int((header or {}).get("version"))
    if version is not None:
        return f"asciicast-v{version}"
    return "cast-header"


def _manifest_time(manifest: dict[str, Any], iso_key: str, ms_key: str) -> Optional[str]:
    return _to_text(manifest.get(iso_key)) or _local_iso_from_epoch_ms(manifest.get(ms_key))


def _guess_project_root(value: Any) -> Optional[str]:
    text = _to_text(value)
    if not text:
        return None

    try:
        path = Path(text).resolve(strict=False)
    except OSError:
        return None

    if _REALM_PROJECT_ROOT not in path.parents and path != _REALM_PROJECT_ROOT:
        return None

    try:
        relative = path.relative_to(_REALM_PROJECT_ROOT)
    except ValueError:
        return None

    if not relative.parts:
        return None

    return str(_REALM_PROJECT_ROOT / relative.parts[0])


def _session_time_from_id(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    match = re.search(r"(\d{13})$", session_id)
    if match:
        return _local_iso_from_epoch_ms(match.group(1))
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", session_id)
    if match:
        try:
            stamp = f"{match.group(1)}T{match.group(2).replace('-', ':')}"
            return datetime.fromisoformat(stamp).astimezone().isoformat()
        except ValueError:
            return None
    match = re.search(r"(\d{8}T\d{6}Z)$", session_id)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone().isoformat()
        except ValueError:
            return None
    return None


def _duration_between(start_value: Any, end_value: Any) -> Optional[float]:
    start = _parse_iso_datetime(_to_text(start_value))
    end = _parse_iso_datetime(_to_text(end_value))
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _assess_session_quality(
    *,
    manifest_exists: bool,
    has_events: bool,
    schema_generation: str,
    created_at: Optional[str],
    finished_at: Optional[str],
    duration_seconds: Optional[float],
    active_seconds: Optional[float],
    command: Optional[str],
    timing_source: Optional[str],
) -> tuple[str, list[str]]:
    flags: list[str] = []

    if not manifest_exists:
        flags.append("missing_manifest")
    if not has_events:
        flags.append("missing_events")
    if not created_at:
        flags.append("missing_created_at")
    if not finished_at:
        flags.append("missing_finished_at")
    if duration_seconds is None:
        flags.append("missing_duration")
    if active_seconds is None:
        flags.append("missing_activity_estimate")
    if not command:
        flags.append("missing_command")
    if timing_source in {"tail", "full-fallback"}:
        flags.append("timing_estimated")
    if timing_source is None:
        flags.append("timing_unavailable")
    if not has_events and not manifest_exists and schema_generation in {"asciicast-v2", "asciicast-v3"}:
        flags.append("header_only")
    if manifest_exists and not has_events:
        flags.append("broken_new_model")

    status = "ok"
    if "broken_new_model" in flags:
        status = "damaged"
    elif "header_only" in flags:
        status = "header-only"
    elif any(
        flag in flags
        for flag in (
            "missing_created_at",
            "missing_finished_at",
            "missing_duration",
            "missing_activity_estimate",
            "timing_unavailable",
        )
    ):
        status = "degraded"

    return status, flags


def _read_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _load_json_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_audio(path: Path) -> Optional[AudioMetadata]:
    stat = path.stat()
    return AudioMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone().isoformat(),
        duration_seconds=None,
        format=path.suffix.lstrip("."),
        channels=None,
        sample_rate=None,
    )


def _parse_screen(path: Path) -> Optional[ScreenMetadata]:
    stat = path.stat()
    return ScreenMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone().isoformat(),
        width=None,
        height=None,
        format=path.suffix.lstrip("."),
    )


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _local_day_bounds(target: date) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    start = datetime.combine(target, datetime.min.time(), tzinfo=local_tz)
    return start, start + timedelta(days=1)


def _local_iso_from_epoch_seconds(value: Any) -> Optional[str]:
    seconds = _to_float(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _local_iso_from_epoch_ms(value: Any) -> Optional[str]:
    millis = _to_float(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ms_to_seconds(value: Any) -> Optional[float]:
    millis = _to_float(value)
    if millis is None:
        return None
    return millis / 1000.0


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
