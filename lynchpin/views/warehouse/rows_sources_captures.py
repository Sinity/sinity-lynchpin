from __future__ import annotations

from typing import Iterable, Iterator, Tuple

from ...sources.captures import activitywatch, atuin, codex, media_capture, terminal_capture, webhistory, webhistory_raw
from .core import WarehouseContext, _json_dumps, _maybe_limit, _parse_dt


def _activitywatch_rows(events: Iterable[activitywatch.ActivityWatchEvent], ctx: WarehouseContext) -> Iterator[Tuple]:
    for event in _maybe_limit(events, ctx.limit):
        yield (
            event.bucket,
            event.start,
            event.end,
            _json_dumps(event.data),
        )


def _activitywatch_events(fn, fn_all, ctx: WarehouseContext) -> Iterable[activitywatch.ActivityWatchEvent]:
    if ctx.since is None and ctx.until is None:
        return fn_all()
    return fn(start=ctx.since, end=ctx.until)


def _atuin_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for cmd in _maybe_limit(atuin.iter_commands(start=ctx.since, end=ctx.until), ctx.limit):
        yield (
            cmd.timestamp,
            cmd.duration_ns,
            cmd.exit_code,
            cmd.cwd,
            cmd.command,
        )


def _codex_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for session in _maybe_limit(codex.iter_sessions(start=ctx.since, end=ctx.until), ctx.limit):
        yield (session.start, str(session.source))


def _instrumentation_terminal_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(terminal_capture.iter_terminal_sessions(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.session_id,
            record.schema_generation,
            _parse_dt(record.created_at),
            _parse_dt(record.finished_at),
            record.duration_seconds,
            record.active_seconds,
            record.idle_seconds,
            record.command_count,
            record.event_count,
            record.command,
            record.title,
            record.shell,
            record.term,
            record.term_type,
            record.term_cols,
            record.term_rows,
            record.host,
            record.user,
            record.terminal,
            record.tty,
            record.start_cwd,
            record.final_cwd,
            record.project_root,
            record.final_project_root,
            record.repo_root,
            record.final_repo_root,
            record.repo_branch,
            record.final_repo_branch,
            record.repo_commit,
            record.final_repo_commit,
            record.repo_dirty,
            record.final_repo_dirty,
            record.exit_code,
            record.exit_reason,
            record.recorder_exit_code,
            record.cleanup_escalated,
            record.manifest_path,
            record.events_path,
            record.has_events,
            record.timing_source,
            record.quality_status,
            _json_dumps(record.quality_flags),
            _json_dumps(record.field_sources),
        )


def _instrumentation_terminal_event_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(terminal_capture.iter_terminal_session_events(), ctx.limit):
        yield (
            record.session_id,
            record.cast_path,
            record.schema_generation,
            record.source,
            _parse_dt(record.time),
            record.type,
            record.pwd,
            record.project_root,
            record.repo_root,
            record.repo_branch,
            record.repo_commit,
            record.repo_dirty,
            record.exit_code,
            record.payload.get("command") or record.payload.get("cmd"),
            record.payload.get("duration_ms"),
            _json_dumps(record.payload),
        )


def _instrumentation_audio_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(media_capture.iter_audio_recordings(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.sha256,
            _parse_dt(record.created_at),
            record.duration_seconds,
            record.format,
            record.channels,
            record.sample_rate,
        )


def _instrumentation_screen_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(media_capture.iter_screenshots(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.sha256,
            _parse_dt(record.created_at),
            record.width,
            record.height,
            record.format,
        )


def _webhistory_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    start_date = ctx.start_date
    end_date = ctx.end_date
    if start_date is None and ctx.since is not None:
        start_date = ctx.since.date().isoformat()
    if end_date is None and ctx.until is not None:
        end_date = ctx.until.date().isoformat()

    for entry in _maybe_limit(
        webhistory.iter_entries(start_date=start_date, end_date=end_date),
        ctx.limit,
    ):
        iso_time = _parse_dt(entry.get("iso_time"))
        url = entry.get("url") or ""
        title = entry.get("title") or ""
        source_file = entry.get("_source_file") or entry.get("source") or ""
        yield (url, title, iso_time, source_file, _json_dumps(entry))


def _webhistory_raw_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for entry in _maybe_limit(webhistory_raw.iter_entries(), ctx.limit):
        yield (
            entry.timestamp,
            entry.url,
            entry.title,
            entry.source_file,
            entry.payload_json,
        )
