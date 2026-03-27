"""Calendar-aligned rollups over context day summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Sequence

from ..signals import ActivitySignal
from .patterns import detect_episodes
from .signal_coverage import compute_coverage
from .summary_models import DaySummary, MonthSummary, QuarterSummary, WeekSummary, YearSummary


def _iso_week_key(target_date: date) -> str:
    iso = target_date.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _classify_day_pattern(days: Sequence[DaySummary]) -> str:
    if not days:
        return "uniform"
    by_weekday = {day.date.weekday(): day.active_seconds for day in days}
    weekday_total = sum(by_weekday.get(index, 0.0) for index in range(5))
    weekend_total = sum(by_weekday.get(index, 0.0) for index in range(5, 7))
    total = weekday_total + weekend_total
    if total < 60:
        return "uniform"
    if weekend_total > weekday_total * 0.8:
        return "weekend_heavy"
    front = sum(by_weekday.get(index, 0.0) for index in range(3))
    back = sum(by_weekday.get(index, 0.0) for index in range(3, 5))
    if front > back * 1.5:
        return "front_loaded"
    if back > front * 1.5:
        return "back_loaded"
    return "uniform"


def summarize_weeks(days: Sequence[DaySummary]) -> list[WeekSummary]:
    if not days:
        return []

    grouped: dict[str, list[DaySummary]] = {}
    for day in days:
        key = _iso_week_key(day.date)
        grouped.setdefault(key, []).append(day)

    weeks: list[WeekSummary] = []
    prior_active: float | None = None
    for week_key in sorted(grouped):
        week_days = sorted(grouped[week_key], key=lambda item: item.date)
        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0

        for day in week_days:
            active_seconds += day.active_seconds
            recovery_seconds += day.recovery_seconds
            chain_count += day.chain_count
            signal_count += day.signal_count
            command_count += day.command_count
            transcript_count += day.transcript_count
            commit_count += day.commit_count
            for mode, seconds in day.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in day.top_projects:
                project_counter[project] += seconds
            for topic, seconds in day.top_topics:
                topic_counter[topic] += seconds

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        busiest = max(week_days, key=lambda item: item.active_seconds) if week_days else None
        quietest = min(week_days, key=lambda item: item.active_seconds) if week_days else None
        delta = (active_seconds - prior_active) if prior_active is not None else None
        weeks.append(
            WeekSummary(
                iso_week=week_key,
                start_date=week_days[0].date,
                end_date=week_days[-1].date,
                days=len(week_days),
                active_seconds=round(active_seconds, 3),
                recovery_seconds=round(recovery_seconds, 3),
                chain_count=chain_count,
                signal_count=signal_count,
                command_count=command_count,
                transcript_count=transcript_count,
                commit_count=commit_count,
                dominant_mode=top_modes[0][0] if top_modes else None,
                dominant_project=top_projects[0][0] if top_projects else None,
                dominant_topic=top_topics[0][0] if top_topics else None,
                top_modes=top_modes,
                top_projects=top_projects,
                top_topics=top_topics,
                day_pattern=_classify_day_pattern(week_days),
                busiest_day=busiest.date if busiest else None,
                quietest_day=quietest.date if quietest else None,
                active_delta_vs_prior=delta,
            )
        )
        prior_active = active_seconds

    return weeks


def summarize_months(
    days: Sequence[DaySummary],
    *,
    signals: Sequence[ActivitySignal] | None = None,
) -> list[MonthSummary]:
    if not days:
        return []

    grouped: dict[str, list[DaySummary]] = defaultdict(list)
    for day in days:
        grouped[day.date.strftime("%Y-%m")].append(day)

    chat_by_month: dict[str, tuple[int, dict[str, int], float]] = {}
    if signals:
        month_sessions: dict[str, set[str]] = defaultdict(set)
        month_work_events: dict[str, Counter[str]] = defaultdict(Counter)
        month_cost: dict[str, float] = defaultdict(float)
        for signal in signals:
            if signal.source != "polylogue.session":
                continue
            month_key = signal.start.strftime("%Y-%m")
            evidence = signal.evidence
            conversation_id = evidence.get("conversation_id")
            if conversation_id:
                month_sessions[month_key].add(str(conversation_id))
            kind = evidence.get("work_event_kind")
            if kind:
                month_work_events[month_key][str(kind)] += 1
            cost = evidence.get("total_cost_usd")
            if isinstance(cost, int | float):
                month_cost[month_key] += float(cost)
        for month_key in grouped:
            chat_by_month[month_key] = (
                len(month_sessions.get(month_key, set())),
                dict(month_work_events.get(month_key, {})),
                month_cost.get(month_key, 0.0),
            )

    months: list[MonthSummary] = []
    prior_active: float | None = None
    for month_key in sorted(grouped):
        month_days = sorted(grouped[month_key], key=lambda item: item.date)
        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        source_counter: Counter[str] = Counter()
        chat_work_event_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0
        active_days = 0

        for day in month_days:
            active_seconds += day.active_seconds
            recovery_seconds += day.recovery_seconds
            chain_count += day.chain_count
            signal_count += day.signal_count
            command_count += day.command_count
            transcript_count += day.transcript_count
            commit_count += day.commit_count
            if day.active_seconds > 0:
                active_days += 1
            for mode, seconds in day.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in day.top_projects:
                project_counter[project] += seconds
            for topic, seconds in day.top_topics:
                topic_counter[topic] += seconds
            source_counter.update(day.source_counts)
            chat_work_event_counter.update(day.chat_work_events)

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])

        coverage_tiers: Counter[str] = Counter()
        for day in month_days:
            coverage = day.signal_coverage or compute_coverage(day)
            coverage_tiers[coverage.quality] += 1

        month_episodes = detect_episodes(month_days)
        month_weeks = summarize_weeks(month_days)
        fallback_chat_session_count = sum(day.chat_session_count for day in month_days)
        fallback_chat_cost = round(sum(day.chat_cost_usd for day in month_days), 4)
        fallback_chat_work_events = dict(chat_work_event_counter)
        chat_session_count, chat_work_events, chat_cost = chat_by_month.get(
            month_key,
            (
                fallback_chat_session_count,
                fallback_chat_work_events,
                fallback_chat_cost,
            ),
        )

        highlights: list[str] = []
        if top_modes:
            highlights.append(f"mode:{top_modes[0][0]} {top_modes[0][1] / 3600.0:.1f}h")
        if top_projects:
            highlights.append(f"project:{top_projects[0][0]} {top_projects[0][1] / 3600.0:.1f}h")
        if command_count:
            highlights.append(f"commands:{command_count}")
        if commit_count:
            highlights.append(f"commits:{commit_count}")
        if chat_session_count:
            highlights.append(f"chat_sessions:{chat_session_count}")

        delta = (active_seconds - prior_active) if prior_active is not None else None
        months.append(
            MonthSummary(
                month=month_key,
                start_date=month_days[0].date,
                end_date=month_days[-1].date,
                total_days=len(month_days),
                active_days=active_days,
                active_seconds=round(active_seconds, 3),
                recovery_seconds=round(recovery_seconds, 3),
                chain_count=chain_count,
                signal_count=signal_count,
                command_count=command_count,
                transcript_count=transcript_count,
                commit_count=commit_count,
                dominant_mode=top_modes[0][0] if top_modes else None,
                dominant_project=top_projects[0][0] if top_projects else None,
                dominant_topic=top_topics[0][0] if top_topics else None,
                top_modes=top_modes,
                top_projects=top_projects,
                top_topics=top_topics,
                source_counts=dict(source_counter),
                coverage_summary=dict(coverage_tiers),
                highlights=highlights[:5],
                chat_session_count=chat_session_count,
                chat_work_events=chat_work_events,
                chat_cost_usd=chat_cost,
                episode_count=len(month_episodes),
                episode_labels=[episode.label for episode in month_episodes],
                week_count=len(month_weeks),
                day_patterns=[week.day_pattern for week in month_weeks],
                active_delta_vs_prior=delta,
            )
        )
        prior_active = active_seconds

    return months


def _quarter_key(month_key: str) -> str:
    year_text, month_text = month_key.split("-")
    quarter = (int(month_text) - 1) // 3 + 1
    return f"{year_text}-Q{quarter}"


def summarize_quarters(months: Sequence[MonthSummary]) -> list[QuarterSummary]:
    if not months:
        return []

    grouped: dict[str, list[MonthSummary]] = {}
    for month in months:
        grouped.setdefault(_quarter_key(month.month), []).append(month)

    quarters: list[QuarterSummary] = []
    prior_active: float | None = None
    for quarter_key in sorted(grouped):
        quarter_months = sorted(grouped[quarter_key], key=lambda item: item.month)
        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        coverage_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0
        total_days = 0
        active_days = 0
        chat_session_count = 0
        chat_cost_usd = 0.0
        episode_count = 0
        month_active_trend: list[float] = []

        for month in quarter_months:
            active_seconds += month.active_seconds
            recovery_seconds += month.recovery_seconds
            chain_count += month.chain_count
            signal_count += month.signal_count
            command_count += month.command_count
            transcript_count += month.transcript_count
            commit_count += month.commit_count
            total_days += month.total_days
            active_days += month.active_days
            chat_session_count += month.chat_session_count
            chat_cost_usd += month.chat_cost_usd
            episode_count += month.episode_count
            month_active_trend.append(month.active_seconds)
            for mode, seconds in month.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in month.top_projects:
                project_counter[project] += seconds
            for topic, seconds in month.top_topics:
                topic_counter[topic] += seconds
            for tier, count in month.coverage_summary.items():
                coverage_counter[tier] += count

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        delta = (active_seconds - prior_active) if prior_active is not None else None
        quarters.append(
            QuarterSummary(
                quarter=quarter_key,
                active_seconds=round(active_seconds, 3),
                recovery_seconds=round(recovery_seconds, 3),
                chain_count=chain_count,
                signal_count=signal_count,
                dominant_mode=top_modes[0][0] if top_modes else None,
                dominant_project=top_projects[0][0] if top_projects else None,
                dominant_topic=top_topics[0][0] if top_topics else None,
                top_modes=top_modes,
                top_projects=top_projects,
                top_topics=top_topics,
                chat_session_count=chat_session_count,
                chat_cost_usd=chat_cost_usd,
                episode_count=episode_count,
                month_count=len(quarter_months),
                month_active_trend=month_active_trend,
                active_delta_vs_prior=delta,
                start_date=quarter_months[0].start_date,
                end_date=quarter_months[-1].end_date,
                total_days=total_days,
                active_days=active_days,
                command_count=command_count,
                transcript_count=transcript_count,
                commit_count=commit_count,
                coverage_summary=dict(coverage_counter),
            )
        )
        prior_active = active_seconds

    return quarters


def summarize_years(quarters: Sequence[QuarterSummary]) -> list[YearSummary]:
    if not quarters:
        return []

    grouped: dict[str, list[QuarterSummary]] = {}
    for quarter in quarters:
        grouped.setdefault(quarter.quarter.split("-")[0], []).append(quarter)

    years: list[YearSummary] = []
    prior_active: float | None = None
    for year_key in sorted(grouped):
        year_quarters = sorted(grouped[year_key], key=lambda item: item.quarter)
        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        coverage_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0
        total_days = 0
        active_days = 0
        chat_session_count = 0
        chat_cost_usd = 0.0
        episode_count = 0
        quarter_active_trend: list[float] = []

        for quarter in year_quarters:
            active_seconds += quarter.active_seconds
            recovery_seconds += quarter.recovery_seconds
            chain_count += quarter.chain_count
            signal_count += quarter.signal_count
            command_count += quarter.command_count
            transcript_count += quarter.transcript_count
            commit_count += quarter.commit_count
            total_days += quarter.total_days
            active_days += quarter.active_days
            chat_session_count += quarter.chat_session_count
            chat_cost_usd += quarter.chat_cost_usd
            episode_count += quarter.episode_count
            quarter_active_trend.append(quarter.active_seconds)
            for mode, seconds in quarter.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in quarter.top_projects:
                project_counter[project] += seconds
            for topic, seconds in quarter.top_topics:
                topic_counter[topic] += seconds
            for tier, count in quarter.coverage_summary.items():
                coverage_counter[tier] += count

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        delta = (active_seconds - prior_active) if prior_active is not None else None
        years.append(
            YearSummary(
                year=year_key,
                active_seconds=round(active_seconds, 3),
                recovery_seconds=round(recovery_seconds, 3),
                chain_count=chain_count,
                signal_count=signal_count,
                dominant_mode=top_modes[0][0] if top_modes else None,
                dominant_project=top_projects[0][0] if top_projects else None,
                dominant_topic=top_topics[0][0] if top_topics else None,
                top_modes=top_modes,
                top_projects=top_projects,
                top_topics=top_topics,
                chat_session_count=chat_session_count,
                chat_cost_usd=chat_cost_usd,
                episode_count=episode_count,
                quarter_count=len(year_quarters),
                quarter_active_trend=quarter_active_trend,
                active_delta_vs_prior=delta,
                start_date=year_quarters[0].start_date,
                end_date=year_quarters[-1].end_date,
                total_days=total_days,
                active_days=active_days,
                command_count=command_count,
                transcript_count=transcript_count,
                commit_count=commit_count,
                coverage_summary=dict(coverage_counter),
            )
        )
        prior_active = active_seconds

    return years
