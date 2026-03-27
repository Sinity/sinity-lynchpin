"""Processed activity-plane row generators."""

from __future__ import annotations

from typing import Iterator, Tuple

from .core import WarehouseContext, _maybe_limit
from .rows_processed_range import _resolve_datetime_range


def _processed_app_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.app_sessions import iter_app_sessions

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for session in _maybe_limit(iter_app_sessions(start=dt_start, end=dt_end), ctx.limit):
        yield (
            session.start.date(),
            session.app,
            session.start,
            session.end,
            session.duration_seconds,
            session.title_dominant,
            session.title_count,
            session.mode,
            session.project,
            session.interruptions,
        )


def _processed_focus_span_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.focus_spans import iter_focus_spans

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for span in _maybe_limit(iter_focus_spans(start=dt_start, end=dt_end), ctx.limit):
        yield (
            span.date,
            span.start,
            span.end,
            span.span_kind,
            span.source_kind,
            span.app,
            span.title,
            span.mode,
            span.project,
            span.duration_seconds,
            span.keypress_count,
            span.changed_keypress_count,
            span.keylog_state,
        )


def _processed_focus_loop_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.focus_loops import iter_focus_loops

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for loop in _maybe_limit(iter_focus_loops(start=dt_start, end=dt_end), ctx.limit):
        yield (
            loop.date,
            loop.start,
            loop.end,
            loop.duration_minutes,
            loop.span_count,
            loop.switch_count,
            loop.cycle_count,
            loop.context_a_app,
            loop.context_a_title,
            loop.context_b_app,
            loop.context_b_title,
            loop.dominant_project,
            loop.dominant_mode,
        )


def _processed_shell_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.shell_sessions import iter_shell_sessions

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for session in _maybe_limit(iter_shell_sessions(start=dt_start, end=dt_end), ctx.limit):
        yield (
            session.start.date(),
            session.cwd,
            session.project,
            session.start,
            session.end,
            session.duration_seconds,
            session.command_count,
            session.error_count,
            session.category,
        )


def _processed_deep_work_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.deep_work import iter_deep_work

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for block in _maybe_limit(iter_deep_work(start=dt_start, end=dt_end), ctx.limit):
        yield (
            block.start.date(),
            block.start,
            block.end,
            block.duration_minutes,
            block.project,
            block.mode,
            block.app_switches,
            block.git_lines_changed,
            block.git_files_changed,
            block.command_count,
            block.focus_ratio,
        )
