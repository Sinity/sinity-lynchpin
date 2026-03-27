from __future__ import annotations

from typing import Iterator, Tuple

from .core import WarehouseContext, _json_dumps, _maybe_limit
from .rows_context_snapshot import _context_rollups_snapshot


def _context_quarter_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, quarters, _, _ = _context_rollups_snapshot(ctx)
    for quarter in _maybe_limit(quarters, ctx.limit):
        yield (
            quarter.quarter,
            quarter.start_date,
            quarter.end_date,
            quarter.total_days,
            quarter.active_days,
            quarter.active_seconds,
            quarter.recovery_seconds,
            quarter.chain_count,
            quarter.signal_count,
            quarter.command_count,
            quarter.transcript_count,
            quarter.commit_count,
            quarter.dominant_mode,
            quarter.dominant_project,
            quarter.dominant_topic,
            _json_dumps([[mode, seconds] for mode, seconds in quarter.top_modes]),
            _json_dumps([[project, seconds] for project, seconds in quarter.top_projects]),
            _json_dumps([[topic, seconds] for topic, seconds in quarter.top_topics]),
            _json_dumps(quarter.coverage_summary),
            quarter.chat_session_count,
            quarter.chat_cost_usd,
            quarter.episode_count,
            quarter.month_count,
            _json_dumps(list(quarter.month_active_trend)),
            quarter.active_delta_vs_prior,
        )


def _context_year_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, years, _ = _context_rollups_snapshot(ctx)
    for year in _maybe_limit(years, ctx.limit):
        yield (
            year.year,
            year.start_date,
            year.end_date,
            year.total_days,
            year.active_days,
            year.active_seconds,
            year.recovery_seconds,
            year.chain_count,
            year.signal_count,
            year.command_count,
            year.transcript_count,
            year.commit_count,
            year.dominant_mode,
            year.dominant_project,
            year.dominant_topic,
            _json_dumps([[mode, seconds] for mode, seconds in year.top_modes]),
            _json_dumps([[project, seconds] for project, seconds in year.top_projects]),
            _json_dumps([[topic, seconds] for topic, seconds in year.top_topics]),
            _json_dumps(year.coverage_summary),
            year.chat_session_count,
            year.chat_cost_usd,
            year.episode_count,
            year.quarter_count,
            _json_dumps(list(year.quarter_active_trend)),
            year.active_delta_vs_prior,
        )


def _context_month_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, _, _, _ = _context_rollups_snapshot(ctx)
    for month in _maybe_limit(months, ctx.limit):
        yield (
            month.month,
            month.start_date,
            month.end_date,
            month.total_days,
            month.active_days,
            month.active_seconds,
            month.recovery_seconds,
            month.chain_count,
            month.signal_count,
            month.command_count,
            month.transcript_count,
            month.commit_count,
            month.dominant_mode,
            month.dominant_project,
            month.dominant_topic,
            _json_dumps([[mode, seconds] for mode, seconds in month.top_modes]),
            _json_dumps([[project, seconds] for project, seconds in month.top_projects]),
            _json_dumps([[topic, seconds] for topic, seconds in month.top_topics]),
            _json_dumps(month.source_counts),
            _json_dumps(month.coverage_summary),
            _json_dumps(list(month.highlights)),
            month.chat_session_count,
            month.chat_cost_usd,
            _json_dumps(month.chat_work_events),
            month.episode_count,
            _json_dumps(list(month.episode_labels)),
            month.week_count,
            _json_dumps(list(month.day_patterns)),
        )


def _context_week_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, _, weeks = _context_rollups_snapshot(ctx)
    for week in _maybe_limit(weeks, ctx.limit):
        yield (
            week.iso_week,
            week.start_date,
            week.end_date,
            week.days,
            week.active_seconds,
            week.recovery_seconds,
            week.observed_seconds,
            week.chain_count,
            week.signal_count,
            week.command_count,
            week.transcript_count,
            week.commit_count,
            week.dominant_mode,
            week.dominant_project,
            week.dominant_topic,
            _json_dumps([[mode, seconds] for mode, seconds in week.top_modes]),
            _json_dumps([[project, seconds] for project, seconds in week.top_projects]),
            _json_dumps([[topic, seconds] for topic, seconds in week.top_topics]),
            week.day_pattern,
            week.busiest_day,
            week.quietest_day,
            week.active_delta_vs_prior,
        )


def _context_period_project_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, quarters, years, weeks = _context_rollups_snapshot(ctx)
    rows: list[Tuple] = []
    for week in weeks:
        for rank, (project_name, seconds) in enumerate(week.top_projects, start=1):
            rows.append((week.iso_week, "week", project_name, seconds, rank))
    for month in months:
        for rank, (project_name, seconds) in enumerate(month.top_projects, start=1):
            rows.append((month.month, "month", project_name, seconds, rank))
    for quarter in quarters:
        for rank, (project_name, seconds) in enumerate(quarter.top_projects, start=1):
            rows.append((quarter.quarter, "quarter", project_name, seconds, rank))
    for year in years:
        for rank, (project_name, seconds) in enumerate(year.top_projects, start=1):
            rows.append((year.year, "year", project_name, seconds, rank))
    yield from _maybe_limit(rows, ctx.limit)


def _context_period_topic_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, quarters, years, weeks = _context_rollups_snapshot(ctx)
    rows: list[Tuple] = []
    for week in weeks:
        for rank, (topic_name, seconds) in enumerate(week.top_topics, start=1):
            rows.append((week.iso_week, "week", topic_name, seconds, rank))
    for month in months:
        for rank, (topic_name, seconds) in enumerate(month.top_topics, start=1):
            rows.append((month.month, "month", topic_name, seconds, rank))
    for quarter in quarters:
        for rank, (topic_name, seconds) in enumerate(quarter.top_topics, start=1):
            rows.append((quarter.quarter, "quarter", topic_name, seconds, rank))
    for year in years:
        for rank, (topic_name, seconds) in enumerate(year.top_topics, start=1):
            rows.append((year.year, "year", topic_name, seconds, rank))
    yield from _maybe_limit(rows, ctx.limit)
