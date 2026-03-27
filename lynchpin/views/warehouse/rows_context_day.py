from __future__ import annotations

from typing import Iterator, Tuple

from .core import WarehouseContext, _json_dumps, _maybe_limit
from .rows_context_snapshot import _context_snapshot


def _context_signal_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    signals, _, _, _ = _context_snapshot(ctx)
    for item in _maybe_limit(signals, ctx.limit):
        yield (
            item.signal_id,
            item.source,
            item.kind,
            item.start,
            item.end,
            item.duration_seconds,
            item.mode,
            item.mode_confidence,
            item.project,
            item.project_confidence,
            item.app,
            item.title,
            item.url,
            item.domain,
            item.cwd,
            item.detail,
            _json_dumps({"signal": item.signal.evidence, "reasons": list(item.reasons)}),
        )


def _context_day_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _context_snapshot(ctx)
    for day in _maybe_limit(days, ctx.limit):
        yield (
            day.date,
            day.active_seconds,
            day.recovery_seconds,
            day.observed_seconds,
            day.chain_count,
            day.signal_count,
            day.command_count,
            day.transcript_count,
            day.commit_count,
            day.dominant_mode,
            day.dominant_project,
            day.dominant_topic,
            _json_dumps([[mode, seconds] for mode, seconds in day.top_modes]),
            _json_dumps([[project, seconds] for project, seconds in day.top_projects]),
            _json_dumps([[topic, seconds] for topic, seconds in day.top_topics]),
            _json_dumps(day.source_counts),
            _json_dumps(day.coverage),
            _json_dumps(list(day.highlights)),
        )


def _context_day_project_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _context_snapshot(ctx)
    rows: list[Tuple] = []
    for day in days:
        for project in day.project_summaries:
            rows.append(
                (
                    project.date,
                    project.project,
                    project.duration_seconds,
                    project.chain_count,
                    _json_dumps([[mode, seconds] for mode, seconds in project.top_modes]),
                )
            )
    yield from _maybe_limit(rows, ctx.limit)


def _context_period_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, _, period = _context_snapshot(ctx)
    yield (
        period.start_date,
        period.end_date,
        period.total_days,
        period.active_seconds,
        period.recovery_seconds,
        period.observed_seconds,
        period.chain_count,
        period.signal_count,
        period.command_count,
        period.transcript_count,
        period.commit_count,
        _json_dumps([[mode, seconds] for mode, seconds in period.dominant_modes]),
        _json_dumps([[project, seconds] for project, seconds in period.dominant_projects]),
        _json_dumps(period.source_counts),
        _json_dumps(period.coverage),
        _json_dumps(list(period.highlights)),
    )


def _context_signal_coverage_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _context_snapshot(ctx)
    for day in _maybe_limit(days, ctx.limit):
        coverage = day.signal_coverage
        if coverage is None:
            continue
        yield (
            coverage.date,
            coverage.has_activitywatch,
            coverage.has_terminal,
            coverage.has_polylogue,
            coverage.has_git,
            coverage.has_atuin,
            coverage.has_web,
            coverage.plane_count,
            coverage.observed_hours,
            coverage.quality,
            _json_dumps(list(coverage.source_names)),
        )


def _context_day_topic_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _context_snapshot(ctx)
    rows: list[Tuple] = []
    for day in days:
        for topic, seconds in day.top_topics:
            rows.append((day.date, topic, round(seconds, 3)))
    yield from _maybe_limit(rows, ctx.limit)


def _context_day_event_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...context.patterns import detect_anomalies, detect_episodes

    _, _, days, _ = _context_snapshot(ctx)

    for anomaly in _maybe_limit(detect_anomalies(days), ctx.limit):
        yield (
            anomaly.date,
            anomaly.kind,
            anomaly.description[:120],
            anomaly.severity,
            _json_dumps(anomaly.evidence or {}),
        )

    episodes = detect_episodes(days)
    for episode in episodes:
        yield (
            episode.start_date,
            "episode_start",
            episode.label,
            episode.confidence,
            _json_dumps({"episode_id": episode.episode_id, "trigger": episode.trigger}),
        )
        yield (
            episode.end_date,
            "episode_end",
            episode.label,
            episode.confidence,
            _json_dumps({"episode_id": episode.episode_id, "day_count_with_dominant": episode.day_count_with_dominant}),
        )
