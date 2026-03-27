"""Processed metrics and cross-source row generators."""

from __future__ import annotations

from typing import Iterator, Tuple

from .core import WarehouseContext, _json_dumps, _maybe_limit
from .rows_processed_range import _bounded_date_range


def _processed_circadian_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.circadian import iter_circadian

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_circadian(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.hour,
            row.active_minutes,
            row.recovery_minutes,
            row.git_lines_changed,
            row.git_files_changed,
            row.command_count,
            row.app_switches,
            row.dominant_mode,
            row.dominant_project,
        )


def _processed_delivery_telemetry_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.delivery_telemetry import iter_delivery_telemetry

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_delivery_telemetry(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.active_hours,
            row.total_commits,
            row.ai_commits,
            row.human_commits,
            row.ai_ratio,
            row.commit_density_per_active_hour,
            row.command_count,
            row.command_density_per_active_hour,
            row.chat_sessions,
            row.chat_engaged_minutes,
            row.chat_minutes_per_active_hour,
            _json_dumps(row.repos),
            _json_dumps(row.ai_models_used),
        )


def _processed_context_switch_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.context_switches import iter_context_switch_metrics

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_context_switch_metrics(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.total_switches,
            row.project_switches,
            row.mode_switches,
            row.alternation_loop_count,
            row.alternation_switches,
            row.alternation_minutes,
            row.alternation_share,
            row.avg_focus_minutes,
            row.longest_focus_minutes,
            row.fragmentation_score,
        )


def _processed_project_attention_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.project_attention import iter_project_attention

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_project_attention(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.entropy,
            row.gini,
            row.top_project,
            row.top_project_share,
            row.project_count,
            row.rotation_speed,
        )


def _processed_chat_activity_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.chat_activity import iter_chat_daily

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_chat_daily(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.provider,
            row.session_count,
            row.total_messages,
            row.total_words,
            row.engaged_minutes,
            row.total_wall_minutes,
            row.dominant_work_kind,
            _json_dumps(row.projects),
        )


def _processed_sleep_correlation_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.sleep_correlation import iter_sleep_correlations

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_sleep_correlations(start=start_d, end=end_d), ctx.limit):
        yield (
            row.sleep_date,
            row.sleep_hours,
            row.sleep_score,
            row.sleep_quality,
            row.segment_count,
            row.workday_active_hours,
            row.workday_lines_changed,
            row.workday_files_changed,
            row.workday_dominant_mode,
            row.workday_deep_work_minutes,
            row.productivity_vs_baseline,
        )
