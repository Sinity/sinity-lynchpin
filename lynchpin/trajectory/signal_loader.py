"""JSONL artefact loading, binary search, and deduplication for trajectory signals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

try:
    import orjson as _orjson
    _fast_loads = _orjson.loads
except ImportError:
    _fast_loads = json.loads  # type: ignore[assignment]

from ..sources.captures.instrumentation import (
    TerminalSessionEvent,
    TerminalSessionMetadata,
)
from .signal import TrajectorySignal, _as_local, _parse_optional_dt

_AW_WINDOW_ARTEFACT_DIR = Path("artefacts/ingest/aw_window")
_AW_WEB_ARTEFACT_DIR = Path("artefacts/ingest/aw_web")
_AW_AFk_ARTEFACT_DIR = Path("artefacts/ingest/aw_afk")
_SESSIONS_ARTEFACT = Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl")
_SESSION_EVENTS_ARTEFACT = Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl")
_POLYLOGUE_ARTEFACT = Path("artefacts/ingest/polylogue/polylogue_signals.jsonl")
_GIT_ARTEFACT = Path("artefacts/ingest/git/git_signals.jsonl")

_BINARY_SEARCH_MIN_FILE = 131072  # 128KB: minimum file size to warrant binary search
_BINARY_SEARCH_GRANULARITY = 8192  # stop when lo/hi are within 8KB
_BINARY_SEARCH_BUFFER = timedelta(hours=1)  # seek to (start - 1h) to handle signals straddling the boundary


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


def _find_start_byte(path: Path, start_dt: datetime, key: str = "start") -> int:
    """Binary search for byte offset near the first record with key >= (start_dt - 1h).

    Handles both ISO 8601 formats (with separators) and compact Z-suffixed formats
    like ``20251224T144544Z`` by replacing trailing Z with +00:00 before parsing.

    Returns the byte offset to seek to before sequential scanning. Guarantees no
    records within the query window are skipped -- the 1-hour buffer handles signals
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
                # Inline fast datetime parse -- all timestamps are ISO format with tz
                try:
                    local_start = datetime.fromisoformat(raw_s).astimezone(_local_tz)
                except (ValueError, TypeError):
                    continue
                # File is chronologically sorted -- break as soon as we pass the window
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
    aw_fetch,  # callable(start, end) -> Iterator[ActivityWatchEvent]
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
    from .signal_sources import _collapse_window_like

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
            # Skip entirely if artefact is fresh (<1h) -- AW DB connection overhead
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
                break  # ascending sort -- all subsequent sessions start after query window
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


def _iter_polylogue_signals_from_jsonl(
    artefact: Path,
    start: datetime,
    end: datetime,
    *,
    seen_conv_ids: set[str],
) -> Iterator[TrajectorySignal]:
    """Read pre-computed polylogue signals from JSONL artefact, filtering to [start, end].

    File is in descending start-time order (newest first), but sessions can span
    multiple days so we cannot break early -- a session starting before `start`
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
