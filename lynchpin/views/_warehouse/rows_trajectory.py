from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterator, Optional, Tuple

from ...trajectory import (
    chains as trajectory_chains,
    day as trajectory_day,
    period as trajectory_period,
    rules as trajectory_rules,
    signal as trajectory_signal,
)
from .core import WarehouseContext, _json_dumps, _maybe_limit, _parse_dt


@lru_cache(maxsize=8)
def _trajectory_rows_window_cached(
    since_text: Optional[str],
    until_text: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    since = _parse_dt(since_text)
    until = _parse_dt(until_text)
    if start_date:
        since = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), tzinfo=local_tz)
    if end_date:
        until = datetime.combine(date.fromisoformat(end_date), datetime.min.time(), tzinfo=local_tz) + timedelta(days=1)
    return trajectory_signal.resolve_window(
        start=since,
        end=until,
        days=trajectory_signal.DEFAULT_LOOKBACK_DAYS,
    )


def _trajectory_rows_window(ctx: WarehouseContext) -> tuple[datetime, datetime]:
    return _trajectory_rows_window_cached(
        ctx.since.isoformat() if ctx.since else None,
        ctx.until.isoformat() if ctx.until else None,
        ctx.start_date,
        ctx.end_date,
    )


@lru_cache(maxsize=8)
def _trajectory_dataset(
    since_text: Optional[str],
    until_text: Optional[str],
) -> tuple[
    tuple[trajectory_rules.AttributedSignal, ...],
    tuple[trajectory_chains.TrajectoryChain, ...],
    tuple[trajectory_day.TrajectoryDay, ...],
    trajectory_period.TrajectoryPeriodSummary,
    tuple,  # raw TrajectorySignal objects
]:
    since = _parse_dt(since_text)
    until = _parse_dt(until_text)
    start, end = trajectory_signal.resolve_window(
        start=since,
        end=until,
        days=trajectory_signal.DEFAULT_LOOKBACK_DAYS,
    )
    raw_signals = tuple(trajectory_signal.load_signals(start=start, end=end, days=trajectory_signal.DEFAULT_LOOKBACK_DAYS))
    attributed = tuple(trajectory_rules.classify_signals(raw_signals))
    chains = tuple(trajectory_chains.build_chains_from_attributed(attributed))
    days = tuple(
        trajectory_day.summarize_days(
            signals=raw_signals,
            chains=chains,
            start=start,
            end=end,
            days=trajectory_signal.DEFAULT_LOOKBACK_DAYS,
        )
    )
    period = trajectory_period.summarize_period(days)
    return attributed, chains, days, period, raw_signals


@lru_cache(maxsize=8)
def _trajectory_months_dataset(
    since_text: Optional[str],
    until_text: Optional[str],
) -> tuple:
    from ..trajectory.month import summarize_months as _summarize_months
    from ..trajectory.quarter import summarize_quarters
    from ..trajectory.year import summarize_years
    from ..trajectory.week import summarize_weeks
    _, _, days, _, raw_signals = _trajectory_dataset(since_text, until_text)
    months = tuple(_summarize_months(days, signals=raw_signals))
    quarters = tuple(summarize_quarters(months))
    years = tuple(summarize_years(quarters))
    weeks = tuple(summarize_weeks(days))
    return months, quarters, years, weeks


def _trajectory_snapshot(ctx: WarehouseContext):
    start, end = _trajectory_rows_window(ctx)
    attributed, chains, days, period, _raw = _trajectory_dataset(start.isoformat(), end.isoformat())
    return attributed, chains, days, period


def _trajectory_months_snapshot(ctx: WarehouseContext) -> tuple:
    """Return cached (months, quarters, years, weeks) without re-loading signals."""
    start, end = _trajectory_rows_window(ctx)
    return _trajectory_months_dataset(start.isoformat(), end.isoformat())


def _trajectory_signal_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    signals, _, _, _ = _trajectory_snapshot(ctx)
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


def _trajectory_chain_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, chains, _, _ = _trajectory_snapshot(ctx)
    for chain in _maybe_limit(chains, ctx.limit):
        yield (
            chain.chain_id,
            chain.start,
            chain.end,
            chain.duration_seconds,
            chain.mode,
            chain.project,
            chain.mode_confidence,
            chain.project_confidence,
            chain.signal_count,
            chain.source_count,
            _json_dumps(list(chain.sources)),
            _json_dumps(list(chain.apps)),
            _json_dumps(list(chain.domains)),
            _json_dumps(list(chain.titles)),
            _json_dumps(list(chain.reasons)),
            chain.topic,
            chain.topic_confidence,
        )


def _trajectory_chain_topic_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, chains, _, _ = _trajectory_snapshot(ctx)
    for chain in chains:
        for topic, seconds in chain.topic_seconds:
            yield (chain.chain_id, topic, seconds, chain.topic_confidence)


def _trajectory_day_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _trajectory_snapshot(ctx)
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


def _trajectory_day_project_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _trajectory_snapshot(ctx)
    rows: list[Tuple] = []
    for day in days:
        for project in day.projects:
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


def _trajectory_period_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, _, period = _trajectory_snapshot(ctx)
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


def _trajectory_signal_coverage_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _trajectory_snapshot(ctx)
    for day in _maybe_limit(days, ctx.limit):
        cov = day.signal_coverage
        if cov is None:
            continue
        yield (
            cov.date,
            cov.has_activitywatch,
            cov.has_terminal,
            cov.has_polylogue,
            cov.has_git,
            cov.has_atuin,
            cov.has_web,
            cov.plane_count,
            cov.observed_hours,
            cov.quality,
            _json_dumps(list(cov.source_names)),
        )


def _trajectory_quarter_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, quarters, _, _ = _trajectory_months_snapshot(ctx)
    for q in _maybe_limit(quarters, ctx.limit):
        yield (
            q.quarter,
            q.start_date,
            q.end_date,
            q.total_days,
            q.active_days,
            q.active_seconds,
            q.recovery_seconds,
            q.chain_count,
            q.signal_count,
            q.command_count,
            q.transcript_count,
            q.commit_count,
            q.dominant_mode,
            q.dominant_project,
            q.dominant_topic,
            _json_dumps([[m, s] for m, s in q.top_modes]),
            _json_dumps([[p, s] for p, s in q.top_projects]),
            _json_dumps([[t, s] for t, s in q.top_topics]),
            _json_dumps(q.coverage_summary),
            q.chat_session_count,
            q.chat_cost_usd,
            q.episode_count,
            q.month_count,
            _json_dumps(list(q.month_active_trend)),
            q.active_delta_vs_prior,
        )


def _trajectory_year_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, years, _ = _trajectory_months_snapshot(ctx)
    for y in _maybe_limit(years, ctx.limit):
        yield (
            y.year,
            y.start_date,
            y.end_date,
            y.total_days,
            y.active_days,
            y.active_seconds,
            y.recovery_seconds,
            y.chain_count,
            y.signal_count,
            y.command_count,
            y.transcript_count,
            y.commit_count,
            y.dominant_mode,
            y.dominant_project,
            y.dominant_topic,
            _json_dumps([[m, s] for m, s in y.top_modes]),
            _json_dumps([[p, s] for p, s in y.top_projects]),
            _json_dumps([[t, s] for t, s in y.top_topics]),
            _json_dumps(y.coverage_summary),
            y.chat_session_count,
            y.chat_cost_usd,
            y.episode_count,
            y.quarter_count,
            _json_dumps(list(y.quarter_active_trend)),
            y.active_delta_vs_prior,
        )


def _trajectory_month_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, _, _, _ = _trajectory_months_snapshot(ctx)
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
            _json_dumps([[m, s] for m, s in month.top_modes]),
            _json_dumps([[p, s] for p, s in month.top_projects]),
            _json_dumps([[t, s] for t, s in month.top_topics]),
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


def _trajectory_week_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, _, weeks = _trajectory_months_snapshot(ctx)
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
            _json_dumps([[m, s] for m, s in week.top_modes]),
            _json_dumps([[p, s] for p, s in week.top_projects]),
            _json_dumps([[t, s] for t, s in week.top_topics]),
            week.day_pattern,
            week.busiest_day,
            week.quietest_day,
            week.active_delta_vs_prior,
        )


def _trajectory_day_topic_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    _, _, days, _ = _trajectory_snapshot(ctx)
    rows: list[Tuple] = []
    for day in days:
        for topic, seconds in day.top_topics:
            rows.append((day.date, topic, round(seconds, 3)))
    yield from _maybe_limit(rows, ctx.limit)


def _trajectory_episode_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ..trajectory.episode import detect_episodes

    _, _, days, _ = _trajectory_snapshot(ctx)
    episodes = detect_episodes(days)
    for ep in _maybe_limit(episodes, ctx.limit):
        yield (
            ep.episode_id,
            ep.label,
            ep.start_date,
            ep.end_date,
            ep.days,
            ep.active_seconds,
            ep.dominant_mode,
            ep.dominant_project,
            ep.dominant_topic,
            _json_dumps(ep.mode_distribution),
            _json_dumps(ep.project_distribution),
            ep.trigger,
            ep.confidence,
            ep.day_count_with_dominant,
        )


def _trajectory_anomaly_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ..trajectory.anomaly import detect_anomalies

    _, _, days, _ = _trajectory_snapshot(ctx)
    anomalies = detect_anomalies(days)
    for a in _maybe_limit(anomalies, ctx.limit):
        yield (
            a.anomaly_id,
            a.date,
            a.kind,
            a.severity,
            a.description,
            a.baseline_value,
            a.actual_value,
            _json_dumps(a.evidence or {}),
        )


def _trajectory_day_event_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ..trajectory.anomaly import detect_anomalies
    from ..trajectory.episode import detect_episodes

    _, _, days, _ = _trajectory_snapshot(ctx)

    # Anomaly events (one row per anomaly)
    for a in _maybe_limit(detect_anomalies(days), ctx.limit):
        yield (
            a.date,
            a.kind,
            a.description[:120],
            a.severity,
            _json_dumps(a.evidence or {}),
        )

    # Episode boundary events (start + end per episode)
    episodes = detect_episodes(days)
    for ep in episodes:
        yield (
            ep.start_date,
            "episode_start",
            ep.label,
            ep.confidence,
            _json_dumps({"episode_id": ep.episode_id, "trigger": ep.trigger}),
        )
        yield (
            ep.end_date,
            "episode_end",
            ep.label,
            ep.confidence,
            _json_dumps({"episode_id": ep.episode_id, "day_count_with_dominant": ep.day_count_with_dominant}),
        )


def _trajectory_period_project_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, quarters, years, weeks = _trajectory_months_snapshot(ctx)

    rows: list[Tuple] = []
    for w in weeks:
        for rank, (project_name, seconds) in enumerate(w.top_projects, start=1):
            rows.append((w.iso_week, "week", project_name, seconds, rank))
    for m in months:
        for rank, (project_name, seconds) in enumerate(m.top_projects, start=1):
            rows.append((m.month, "month", project_name, seconds, rank))
    for q in quarters:
        for rank, (project_name, seconds) in enumerate(q.top_projects, start=1):
            rows.append((q.quarter, "quarter", project_name, seconds, rank))
    for y in years:
        for rank, (project_name, seconds) in enumerate(y.top_projects, start=1):
            rows.append((y.year, "year", project_name, seconds, rank))

    yield from _maybe_limit(rows, ctx.limit)


def _trajectory_period_topic_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    months, quarters, years, weeks = _trajectory_months_snapshot(ctx)

    rows: list[Tuple] = []
    for w in weeks:
        for rank, (topic_name, seconds) in enumerate(w.top_topics, start=1):
            rows.append((w.iso_week, "week", topic_name, seconds, rank))
    for m in months:
        for rank, (topic_name, seconds) in enumerate(m.top_topics, start=1):
            rows.append((m.month, "month", topic_name, seconds, rank))
    for q in quarters:
        for rank, (topic_name, seconds) in enumerate(q.top_topics, start=1):
            rows.append((q.quarter, "quarter", topic_name, seconds, rank))
    for y in years:
        for rank, (topic_name, seconds) in enumerate(y.top_topics, start=1):
            rows.append((y.year, "year", topic_name, seconds, rank))

    yield from _maybe_limit(rows, ctx.limit)
