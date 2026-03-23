"""Row generators for processed source modules → warehouse tables."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Tuple

from .core import WarehouseContext, _json_dumps, _maybe_limit


def _resolve_date_range(ctx: WarehouseContext) -> tuple[date | None, date | None]:
    """Extract start/end dates from warehouse context."""
    start_d: date | None = None
    end_d: date | None = None
    if ctx.start_date:
        start_d = date.fromisoformat(ctx.start_date)
    elif ctx.since:
        start_d = ctx.since.date()
    if ctx.end_date:
        end_d = date.fromisoformat(ctx.end_date)
    elif ctx.until:
        end_d = ctx.until.date()
    return start_d, end_d


def _resolve_datetime_range(ctx: WarehouseContext) -> tuple[datetime, datetime]:
    """Extract start/end datetimes, defaulting to a wide range."""
    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    dt_start = datetime(start_d.year, start_d.month, start_d.day)
    dt_end = datetime(end_d.year, end_d.month, end_d.day) + timedelta(days=1)
    return dt_start, dt_end


def _processed_app_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.app_sessions import iter_app_sessions

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for s in _maybe_limit(iter_app_sessions(start=dt_start, end=dt_end), ctx.limit):
        yield (
            s.start.date(),
            s.app,
            s.start,
            s.end,
            s.duration_seconds,
            s.title_dominant,
            s.title_count,
            s.mode,
            s.project,
            s.interruptions,
        )


def _processed_shell_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.shell_sessions import iter_shell_sessions

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for s in _maybe_limit(iter_shell_sessions(start=dt_start, end=dt_end), ctx.limit):
        yield (
            s.start.date(),
            s.cwd,
            s.project,
            s.start,
            s.end,
            s.duration_seconds,
            s.command_count,
            s.error_count,
            s.category,
        )


def _processed_git_daily_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_activity import iter_git_daily

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for g in _maybe_limit(iter_git_daily(start=start_d, end=end_d), ctx.limit):
        yield (
            g.date,
            g.repo,
            g.commit_count,
            g.lines_added,
            g.lines_deleted,
            g.churn,
            g.net_loc,
            g.ai_coauthored,
            g.ai_ratio,
            g.dominant_prefix,
            g.commit_burst_count,
        )


def _processed_deep_work_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.deep_work import iter_deep_work

    dt_start, dt_end = _resolve_datetime_range(ctx)
    for b in _maybe_limit(iter_deep_work(start=dt_start, end=dt_end), ctx.limit):
        yield (
            b.start.date(),
            b.start,
            b.end,
            b.duration_minutes,
            b.project,
            b.mode,
            b.app_switches,
            b.commit_count,
            b.command_count,
            b.focus_ratio,
        )


def _processed_circadian_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.circadian import iter_circadian

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for p in _maybe_limit(iter_circadian(start=start_d, end=end_d), ctx.limit):
        yield (
            p.date,
            p.hour,
            p.active_minutes,
            p.recovery_minutes,
            p.commit_count,
            p.command_count,
            p.app_switches,
            p.dominant_mode,
            p.dominant_project,
        )


def _processed_delegation_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.delegation import iter_delegation_metrics

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for m in _maybe_limit(iter_delegation_metrics(start=start_d, end=end_d), ctx.limit):
        yield (
            m.date,
            m.total_commits,
            m.ai_commits,
            m.human_commits,
            m.ai_ratio,
            m.commits_per_tracked_hour,
            m.delegation_mode,
            m.chat_sessions,
            m.chat_minutes,
        )


def _processed_context_switch_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.context_switches import iter_context_switch_metrics

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for m in _maybe_limit(iter_context_switch_metrics(start=start_d, end=end_d), ctx.limit):
        yield (
            m.date,
            m.total_switches,
            m.project_switches,
            m.mode_switches,
            m.avg_focus_minutes,
            m.longest_focus_minutes,
            m.fragmentation_score,
        )


def _processed_project_attention_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.project_attention import iter_project_attention

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for m in _maybe_limit(iter_project_attention(start=start_d, end=end_d), ctx.limit):
        yield (
            m.date,
            m.entropy,
            m.gini,
            m.top_project,
            m.top_project_share,
            m.project_count,
            m.rotation_speed,
        )


def _processed_chat_activity_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.chat_activity import iter_chat_daily

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for c in _maybe_limit(iter_chat_daily(start=start_d, end=end_d), ctx.limit):
        yield (
            c.date,
            c.provider,
            c.session_count,
            c.total_messages,
            c.total_words,
            c.total_wall_minutes,
            c.dominant_work_kind,
            _json_dumps(c.projects),
        )


def _processed_sleep_correlation_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.sleep_correlation import iter_sleep_correlations

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for s in _maybe_limit(iter_sleep_correlations(start=start_d, end=end_d), ctx.limit):
        yield (
            s.sleep_date,
            s.sleep_hours,
            s.sleep_score,
            s.sleep_quality,
            s.next_day_active_hours,
            s.next_day_commits,
            s.next_day_dominant_mode,
            s.next_day_deep_work_minutes,
            s.productivity_vs_baseline,
        )


def _processed_commit_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_activity import iter_commit_sessions

    start_d, end_d = _resolve_date_range(ctx)
    if start_d is None:
        start_d = date(2020, 1, 1)
    if end_d is None:
        end_d = date(2030, 1, 1)
    for s in _maybe_limit(iter_commit_sessions(start=start_d, end=end_d), ctx.limit):
        yield (
            s.repo,
            s.start,
            s.end,
            s.commits,
            s.is_burst,
            s.ai_fraction,
            s.lines_changed,
        )
