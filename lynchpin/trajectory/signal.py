from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence
from urllib.parse import urlparse

try:
    import orjson as _orjson
    _fast_loads = _orjson.loads
except ImportError:
    _fast_loads = json.loads  # type: ignore[assignment]

from ..core.projects import ALL_PROJECTS
from ..sources.captures import activitywatch, atuin, instrumentation
from ..sources.captures.instrumentation import (
    TerminalSessionEvent,
    TerminalSessionMetadata,
    iter_terminal_sessions_fast,
)
from ..sources.indices import gitstats

_SESSIONS_ARTEFACT = Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl")
# Pre-resolved project paths and name lookup — computed once at import
_PROJECT_RESOLVED_PATHS: list[tuple[str, Path]] = [
    (entry.name, Path(entry.path).expanduser().resolve(strict=False))
    for entry in ALL_PROJECTS.values()
]
_PROJECT_NAMES_SORTED: list[str] = sorted(ALL_PROJECTS, key=len, reverse=True)
_SESSION_EVENTS_ARTEFACT = Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl")
_POLYLOGUE_ARTEFACT = Path("artefacts/ingest/polylogue/polylogue_signals.jsonl")
_GIT_ARTEFACT = Path("artefacts/ingest/git/git_signals.jsonl")
_AW_WINDOW_ARTEFACT_DIR = Path("artefacts/ingest/aw_window")
_AW_WEB_ARTEFACT_DIR = Path("artefacts/ingest/aw_web")

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
    yield from _polylogue_session_signals(start, end)
    yield from _git_commit_signals(start, end)


def _aw_monthly_artefact_cutover(artefact_dir: Path) -> Optional[datetime]:
    """Return the mtime of the most recent monthly artefact file as the cutover timestamp."""
    if not artefact_dir.exists():
        return None
    files = sorted(artefact_dir.glob("????-??.jsonl"))
    if not files:
        return None
    return datetime.fromtimestamp(files[-1].stat().st_mtime, tz=timezone.utc)


def _iter_months(start: datetime, end: datetime) -> Iterator[tuple[int, int]]:
    """Yield (year, month) pairs for every calendar month that overlaps [start, end]."""
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=start.tzinfo)
    while cur <= end:
        yield cur.year, cur.month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


_BINARY_SEARCH_MIN_FILE = 131072  # 128KB: minimum file size to warrant binary search
_BINARY_SEARCH_GRANULARITY = 8192  # stop when lo/hi are within 8KB
_BINARY_SEARCH_BUFFER = timedelta(hours=1)  # seek to (start - 1h) to handle signals straddling the boundary


def _find_start_byte(path: Path, start_dt: datetime, key: str = "start") -> int:
    """Binary search for byte offset near the first record with key >= (start_dt - 1h).

    Handles both ISO 8601 formats (with separators) and compact Z-suffixed formats
    like ``20251224T144544Z`` by replacing trailing Z with +00:00 before parsing.

    Returns the byte offset to seek to before sequential scanning. Guarantees no
    records within the query window are skipped — the 1-hour buffer handles signals
    whose start is slightly before the query window but end within it.

    Returns 0 if the file is too small to warrant binary search.
    """
    file_size = path.stat().st_size
    if file_size < _BINARY_SEARCH_MIN_FILE:
        return 0
    target = start_dt - _BINARY_SEARCH_BUFFER
    lo, hi = 0, file_size
    with path.open("rb") as fh:
        while hi - lo > _BINARY_SEARCH_GRANULARITY:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()  # discard partial line to align to next \n
            line = fh.readline()
            if not line:
                hi = mid
                continue
            try:
                raw_s = _fast_loads(line.strip()).get(key, "")
                if not raw_s:
                    lo = mid
                    continue
                if isinstance(raw_s, str) and raw_s.endswith("Z"):
                    raw_s = raw_s[:-1] + "+00:00"
                ts = datetime.fromisoformat(raw_s)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=target.tzinfo)
                else:
                    ts = ts.astimezone(target.tzinfo)
                if ts < target:
                    lo = mid
                else:
                    hi = mid
            except Exception:
                lo = mid
    return lo


def _iter_aw_signals_from_monthly_jsonl(
    artefact_dir: Path,
    start: datetime,
    end: datetime,
    *,
    seen_signal_ids: set[str],
) -> Iterator[TrajectorySignal]:
    """Read pre-collapsed AW signals from monthly partitioned JSONL files.

    Files are written in chronological order. Binary search jumps past records
    before (start - 1h), then sequential scan with early termination once
    signal_start >= end.
    """
    if not artefact_dir.exists():
        return
    for year, month in _iter_months(start, end):
        path = artefact_dir / f"{year:04d}-{month:02d}.jsonl"
        if not path.exists():
            continue
        _local_tz = start.tzinfo
        seek_to = _find_start_byte(path, start)
        with path.open("rb") as fh:
            if seek_to > 0:
                fh.seek(seek_to)
                fh.readline()  # discard partial line at seek boundary
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    d = _fast_loads(raw_line)
                except (ValueError, KeyError):
                    continue
                raw_s = d.get("start")
                raw_e = d.get("end")
                if not raw_s or not raw_e:
                    continue
                # Inline fast datetime parse — all timestamps are ISO format with tz
                try:
                    local_start = datetime.fromisoformat(raw_s).astimezone(_local_tz)
                except (ValueError, TypeError):
                    continue
                # File is chronologically sorted — break as soon as we pass the window
                if local_start >= end:
                    break
                try:
                    local_end = datetime.fromisoformat(raw_e).astimezone(_local_tz)
                except (ValueError, TypeError):
                    continue
                if local_end <= start:
                    continue
                sig_id = d.get("signal_id") or ""
                if sig_id:
                    seen_signal_ids.add(sig_id)
                yield TrajectorySignal(
                    signal_id=sig_id,
                    source=d.get("source") or "",
                    kind=d.get("kind") or "",
                    start=local_start,
                    end=local_end,
                    mode_hint=d.get("mode_hint"),
                    project_hint=d.get("project_hint"),
                    app=d.get("app"),
                    title=d.get("title"),
                    url=d.get("url"),
                    domain=d.get("domain"),
                    cwd=d.get("cwd"),
                    detail=d.get("detail"),
                    evidence=d.get("evidence") or {},
                )


def _aw_signals_mixed(
    artefact_dir: Path,
    start: datetime,
    end: datetime,
    *,
    source: str,
    kind: str,
    aw_fetch,  # callable(start, end) → Iterator[ActivityWatchEvent]
    app_key: Optional[str],
    title_key: Optional[str],
    url_key: Optional[str],
) -> Iterator[TrajectorySignal]:
    """Yield AW signals, using monthly JSONL artefacts where available and DB for gaps.

    For months with an artefact file: read the pre-collapsed JSONL (fast, no DB hit).
    For months without an artefact file: query the AW DB and collapse on the fly.
    A live-path overlap covers the last 6h of the most recent artefact month to
    pick up any events captured since the last `just ingest-aw` run.
    """
    from calendar import monthrange as _monthrange

    seen_signal_ids: set[str] = set()

    # Identify which months are covered by artefact files
    covered_months: set[tuple[int, int]] = set()
    for year, month in _iter_months(start, end):
        if (artefact_dir / f"{year:04d}-{month:02d}.jsonl").exists():
            covered_months.add((year, month))

    # Fast path: read artefact files for covered months
    if covered_months:
        yield from _iter_aw_signals_from_monthly_jsonl(
            artefact_dir, start, end, seen_signal_ids=seen_signal_ids
        )

    # DB fallback for uncovered months + live overlap for the latest artefact period
    cutover = _aw_monthly_artefact_cutover(artefact_dir)

    for year, month in _iter_months(start, end):
        _, last_day = _monthrange(year, month)
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        month_end = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc) + timedelta(microseconds=1)
        q_start = max(start, month_start)
        q_end = min(end, month_end)

        if (year, month) not in covered_months:
            # No artefact: fetch from DB and collapse
            events = list(aw_fetch(start=q_start, end=q_end))
            for sig in _collapse_window_like(
                source=source,
                kind=kind,
                events=events,
                app_key=app_key,
                title_key=title_key,
                url_key=url_key,
            ):
                if sig.signal_id not in seen_signal_ids:
                    yield sig
        elif cutover is not None and (year, month) == max(covered_months):
            # Current month artefact may be stale: overlap the last 6h from DB.
            # Skip entirely if artefact is fresh (<1h) — AW DB connection overhead
            # costs ~0.4-0.6s even for small time windows.
            now_utc = datetime.now(timezone.utc)
            if now_utc - cutover < timedelta(hours=1):
                continue
            live_start = max(q_start, cutover - timedelta(hours=6))
            if live_start < q_end:
                events = list(aw_fetch(start=live_start, end=q_end))
                for sig in _collapse_window_like(
                    source=source,
                    kind=kind,
                    events=events,
                    app_key=app_key,
                    title_key=title_key,
                    url_key=url_key,
                ):
                    if sig.signal_id not in seen_signal_ids:
                        yield sig


_AW_AFk_ARTEFACT_DIR = Path("artefacts/ingest/aw_afk")


def _window_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
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


def _web_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
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
) -> Iterator[TrajectorySignal]:
    # Accumulate into mutable locals during a run; create TrajectorySignal only at yield.
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

    def _emit() -> TrajectorySignal:
        assert cur_signal_id is not None
        assert cur_start is not None
        assert cur_end is not None
        return TrajectorySignal(
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
) -> Iterator[TrajectorySignal]:
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
        yield TrajectorySignal(
            signal_id=sig_id,
            source="activitywatch.afk",
            kind="afk",
            start=signal_start,
            end=signal_end,
            mode_hint="recovery",
            detail=status,
            evidence={"bucket": event.bucket, "status": status},
        )


def _afk_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
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
        # No artefact for this month — query DB
        _, last_day = _monthrange(year, month)
        m_start = datetime(year, month, 1, tzinfo=timezone.utc)
        m_end = (
            datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
            + timedelta(microseconds=1)
        )
        yield from _afk_db_signals(max(start, m_start), min(end, m_end), seen_ids)
    # Live overlap: AFK events not yet in the artefact.
    # Skip if artefact is fresh (< 1h old) — DB connection overhead (~0.4s) outweighs benefit.
    # For stale artefacts (> 1h), DB query covers the uncached window.
    if cutover is not None and latest_covered is not None:
        now_utc = datetime.now(timezone.utc)
        if now_utc - cutover > timedelta(hours=1):
            live_start = max(start, cutover - timedelta(minutes=30))
            if live_start < end:
                yield from _afk_db_signals(live_start, end, seen_ids)


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


def _iter_sessions_from_jsonl(
    artefact: Path,
    start: datetime,
    end: datetime,
    *,
    seen_ids: set[str],
) -> Iterator[TerminalSessionMetadata]:
    """Read TerminalSessionMetadata from a pre-computed JSONL artefact.

    File is sorted by created_at ascending. Binary search jumps past records
    before (start - 1h), then sequential scan with early termination.
    """
    if not artefact.exists():
        return
    seek_to = _find_start_byte(artefact, start, key="created_at")
    _local_tz = start.tzinfo
    with artefact.open("rb") as fh:
        if seek_to > 0:
            fh.seek(seek_to)
            fh.readline()
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                d = _fast_loads(raw_line)
            except (ValueError, KeyError):
                continue
            created_at = _parse_optional_dt(d.get("created_at"))
            if created_at is None:
                continue
            local_created = _as_local(created_at)
            if local_created >= end:
                break  # ascending sort — all subsequent sessions start after query window
            finished_at = _parse_optional_dt(d.get("finished_at")) or created_at
            duration = d.get("duration_seconds") or 0.0
            session_end = finished_at if finished_at > created_at else (created_at + timedelta(seconds=duration))
            if _as_local(session_end) <= start:
                continue
            sid = d.get("session_id", "")
            seen_ids.add(sid)
            yield TerminalSessionMetadata(
                session_id=sid,
                path=d.get("path", ""),
                manifest_path=d.get("manifest_path"),
                events_path=d.get("events_path"),
                size_bytes=d.get("size_bytes") or 0,
                created_at=d.get("created_at"),
                finished_at=d.get("finished_at"),
                duration_seconds=d.get("duration_seconds"),
                active_seconds=d.get("active_seconds"),
                idle_seconds=d.get("idle_seconds"),
                command_count=d.get("command_count"),
                event_count=d.get("event_count"),
                command=d.get("command"),
                title=d.get("title"),
                shell=d.get("shell"),
                term=d.get("term"),
                term_type=d.get("term_type"),
                term_cols=d.get("term_cols"),
                term_rows=d.get("term_rows"),
                host=d.get("host"),
                user=d.get("user"),
                tty=d.get("tty"),
                terminal=d.get("terminal"),
                start_cwd=d.get("start_cwd"),
                final_cwd=d.get("final_cwd"),
                project_root=d.get("project_root"),
                final_project_root=d.get("final_project_root"),
                repo_root=d.get("repo_root"),
                final_repo_root=d.get("final_repo_root"),
                repo_branch=d.get("repo_branch"),
                final_repo_branch=d.get("final_repo_branch"),
                repo_commit=d.get("repo_commit"),
                final_repo_commit=d.get("final_repo_commit"),
                repo_dirty=d.get("repo_dirty"),
                final_repo_dirty=d.get("final_repo_dirty"),
                exit_code=d.get("exit_code"),
                exit_reason=d.get("exit_reason"),
                recorder_exit_code=d.get("recorder_exit_code"),
                cleanup_escalated=d.get("cleanup_escalated"),
                has_events=d.get("has_events", False),
                timing_source=d.get("timing_source"),
                schema_generation=d.get("schema_generation", ""),
                quality_status=d.get("quality_status", ""),
                quality_flags=d.get("quality_flags") or [],
                field_sources=d.get("field_sources") or {},
            )


def _iter_events_from_jsonl(
    artefact: Path,
    start: datetime,
    end: datetime,
) -> Iterator[TerminalSessionEvent]:
    """Read TerminalSessionEvent from a pre-computed JSONL artefact.

    File is sorted by event time ascending. Binary search jumps past records
    before (start - 1h), then sequential scan with early termination.
    """
    if not artefact.exists():
        return
    seek_to = _find_start_byte(artefact, start, key="time")
    _local_tz = start.tzinfo
    with artefact.open("rb") as fh:
        if seek_to > 0:
            fh.seek(seek_to)
            fh.readline()
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                d = _fast_loads(raw_line)
            except (ValueError, KeyError):
                continue
            raw_time = d.get("time")
            if not raw_time:
                continue
            try:
                event_local = datetime.fromisoformat(
                    raw_time if not raw_time.endswith("Z") else raw_time[:-1] + "+00:00"
                ).astimezone(_local_tz)
            except (ValueError, TypeError):
                event_time = _parse_optional_dt(raw_time)
                if event_time is None:
                    continue
                event_local = _as_local(event_time)
            if event_local >= end:
                break
            if event_local < start:
                continue
            yield TerminalSessionEvent(
                session_id=d.get("session_id", ""),
                cast_path=d.get("cast_path", ""),
                schema_generation=d.get("schema_generation", ""),
                source=d.get("source", "events_jsonl"),
                time=d.get("time"),
                type=d.get("type", "unknown"),
                pwd=d.get("pwd"),
                project_root=d.get("project_root"),
                repo_root=d.get("repo_root"),
                repo_branch=d.get("repo_branch"),
                repo_commit=d.get("repo_commit"),
                repo_dirty=d.get("repo_dirty"),
                exit_code=d.get("exit_code"),
                payload=d.get("payload") or {},
            )


def _artefact_cutover(artefact: Path) -> Optional[datetime]:
    """Return the mtime of the artefact as a cutover timestamp."""
    if not artefact.exists():
        return None
    return datetime.fromtimestamp(artefact.stat().st_mtime, tz=timezone.utc)


def _session_to_signal(
    session: TerminalSessionMetadata,
    start: datetime,
    end: datetime,
) -> Iterator[TrajectorySignal]:
    """Convert a TerminalSessionMetadata to a TrajectorySignal if it overlaps [start, end]."""
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


def _terminal_session_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
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


def _event_to_signal(event: TerminalSessionEvent, start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    """Convert a TerminalSessionEvent to a TrajectorySignal if it is a command_start in [start, end]."""
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


def _terminal_command_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    # Fast path: pre-computed JSONL artefact
    seen_event_ids: set[str] = set()
    for event in _iter_events_from_jsonl(_SESSION_EVENTS_ARTEFACT, start, end):
        eid = f"{event.session_id}:{event.time}:{event.type}"
        seen_event_ids.add(eid)
        yield from _event_to_signal(event, start, end)
    # Live path: recent events not yet in artefact
    cutover = _artefact_cutover(_SESSION_EVENTS_ARTEFACT)
    live_start = max(start, cutover - timedelta(days=1)) if cutover else start
    for event in instrumentation.iter_terminal_session_events(start=live_start, end=end):
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


def _profile_to_signals(profile: object) -> Iterator[TrajectorySignal]:
    """Convert a SessionProfile to TrajectorySignals (one per work event, or one chat signal)."""
    if not profile.work_events:  # type: ignore[union-attr]
        signal_start = _as_local(profile.first_message_at or profile.created_at) if (profile.first_message_at or profile.created_at) else None  # type: ignore[union-attr]
        if signal_start is None:
            return
        signal_end = _as_local(profile.last_message_at or profile.updated_at) if (profile.last_message_at or profile.updated_at) else signal_start + _TRANSCRIPT_SIGNAL_SECONDS  # type: ignore[union-attr]
        if signal_end <= signal_start:
            signal_end = signal_start + _TRANSCRIPT_SIGNAL_SECONDS
        yield TrajectorySignal(
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

        yield TrajectorySignal(
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


def _iter_polylogue_signals_from_jsonl(
    artefact: Path,
    start: datetime,
    end: datetime,
    *,
    seen_conv_ids: set[str],
) -> Iterator[TrajectorySignal]:
    """Read pre-computed polylogue signals from JSONL artefact, filtering to [start, end].

    File is in descending start-time order (newest first), but sessions can span
    multiple days so we cannot break early — a session starting before `start`
    may still have end > start. Full scan is required (~6MB, ~0.03s).
    """
    if not artefact.exists():
        return
    _local_tz = start.tzinfo
    with artefact.open("rb") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                d = _fast_loads(raw_line)
            except (ValueError, KeyError):
                continue
            raw_s = d.get("start")
            raw_e = d.get("end")
            if not raw_s or not raw_e:
                continue
            try:
                local_start = datetime.fromisoformat(raw_s).astimezone(_local_tz)
            except (ValueError, TypeError):
                local_start_opt = _parse_optional_dt(raw_s)
                if local_start_opt is None:
                    continue
                local_start = local_start_opt
            if local_start >= end:
                continue
            try:
                local_end = datetime.fromisoformat(raw_e).astimezone(_local_tz)
            except (ValueError, TypeError):
                local_end_opt = _parse_optional_dt(raw_e)
                if local_end_opt is None:
                    continue
                local_end = local_end_opt
            if local_end <= start:
                continue
            conv_id = (d.get("evidence") or {}).get("conversation_id", "")
            if conv_id:
                seen_conv_ids.add(conv_id)
            yield TrajectorySignal(
                signal_id=d.get("signal_id") or "",
                source=d.get("source") or "polylogue.session",
                kind=d.get("kind") or "session",
                start=local_start,
                end=local_end,
                mode_hint=d.get("mode_hint"),
                project_hint=d.get("project_hint"),
                app=d.get("app"),
                title=d.get("title"),
                url=d.get("url"),
                domain=d.get("domain"),
                cwd=d.get("cwd"),
                detail=d.get("detail"),
                evidence=d.get("evidence") or {},
            )


def _polylogue_session_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    seen_conv_ids: set[str] = set()
    # Fast path: pre-computed JSONL artefact (ms instead of 20+ seconds)
    yield from _iter_polylogue_signals_from_jsonl(_POLYLOGUE_ARTEFACT, start, end, seen_conv_ids=seen_conv_ids)
    # Live path: only conversations NOT already in the artefact.
    # Skip entirely if artefact is fresh (<2h) — polylogue DB connection costs ~0.44s
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
) -> Iterator[TrajectorySignal]:
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
        # Filter at summary level — avoids fetching full conversation blobs for known IDs
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


def _numstat_record_to_signal(record: dict[str, object]) -> Optional[TrajectorySignal]:
    """Convert a single iter_numstat record to a TrajectorySignal, or None if invalid."""
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
    return TrajectorySignal(
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


def _iter_git_signals_from_jsonl(
    artefact: Path,
    start: datetime,
    end: datetime,
    *,
    seen_shas: set[str],
) -> Iterator[TrajectorySignal]:
    """Read pre-computed git commit signals from JSONL artefact.

    File is chronologically sorted; breaks early when signal_start >= end.
    """
    if not artefact.exists():
        return
    _local_tz = start.tzinfo
    with artefact.open("rb") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                d = _fast_loads(raw_line)
            except (ValueError, KeyError):
                continue
            raw_s = d.get("start")
            raw_e = d.get("end")
            if not raw_s or not raw_e:
                continue
            try:
                local_start = datetime.fromisoformat(raw_s).astimezone(_local_tz)
            except (ValueError, TypeError):
                continue
            try:
                local_end = datetime.fromisoformat(raw_e).astimezone(_local_tz)
            except (ValueError, TypeError):
                continue
            if local_end <= start or local_start >= end:
                continue
            sha = (d.get("evidence") or {}).get("commit", "")
            if sha:
                seen_shas.add(sha)
            yield TrajectorySignal(
                signal_id=d.get("signal_id") or "",
                source=d.get("source") or "git.commit",
                kind=d.get("kind") or "git_commit",
                start=local_start,
                end=local_end,
                mode_hint=d.get("mode_hint"),
                project_hint=d.get("project_hint"),
                app=None,
                title=None,
                url=None,
                domain=None,
                cwd=None,
                detail=d.get("detail"),
                evidence=d.get("evidence") or {},
            )


def _git_commit_signals(start: datetime, end: datetime) -> Iterator[TrajectorySignal]:
    seen_shas: set[str] = set()
    # Fast path: pre-computed JSONL artefact (ms instead of 3-5s git subprocesses)
    yield from _iter_git_signals_from_jsonl(_GIT_ARTEFACT, start, end, seen_shas=seen_shas)
    # Live path: commits newer than the artefact cutover.
    # Skip if artefact is fresh (<1h) — git subprocess overhead costs ~0.15s per run.
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


@functools.lru_cache(maxsize=4096)
def _resolve_project_hint(text: str) -> Optional[str]:
    """Cached: resolve a single path/text string to a project name."""
    if not text:
        return None
    if text.startswith("/realm/project/"):
        name = text[len("/realm/project/"):].split("/", 1)[0]
        if name in ALL_PROJECTS:
            return name
    try:
        path = Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None
    for name, project_path in _PROJECT_RESOLVED_PATHS:
        if path == project_path or project_path in path.parents:
            return name
    return None


def _project_hint_from_paths(*values: object) -> Optional[str]:
    for value in values:
        text = _text(value)
        if not text:
            continue
        result = _resolve_project_hint(text)
        if result:
            return result
    return None


def _project_hint_from_text(value: object) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    lowered = text.lower()
    for name in _PROJECT_NAMES_SORTED:
        if name.lower() in lowered:
            return name
    return None


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_local_tz())
    return value.astimezone(_local_tz())


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc
