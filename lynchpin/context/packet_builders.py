"""Context packet composition from bulk evidence-window queries."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from ..periods import key_for_date, parse_period, period_keys_in_range
from .bundles import query_evidence_range
from .day_rollups import bundle_for_day, day_summary_from_summary, group_query_rows_by_day
from .packet_types import (
    ContextPacketMeta,
    CoveragePacket,
    DayPacket,
    EpisodePacket,
    MonthPacket,
    ProjectPacket,
    QuarterPacket,
    ThreadPacket,
    WeekPacket,
    YearPacket,
    ThemePacket,
    ClaimPacket,
    ClaimsPacket,
    ProjectArcPacket,
)
from .patterns import build_recent_focus_loops, detect_anomalies, detect_episodes
from .reports import summarize_evidence_surfaces
from .summary_models import (
    ChainSummary,
    DaySummary,
    EpisodeSummary,
    MonthSummary,
    QuarterSummary,
    WeekSummary,
    YearSummary,
)
from .themes import detect_themes
from .claims import generate_claims
from .project_arcs import build_project_arcs
from .memory import load_memory, build_memory_packet
from .trust import inspect_core_surface_freshness, open_warehouse_read_only

_SCHEMA_VERSION = "lynchpin-context-state-v3"


def _meta(tier: str) -> ContextPacketMeta:
    return ContextPacketMeta(
        schema=_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        budget_tier=tier,
    )


def _top_n(items: tuple[tuple[str, float], ...], tier: str) -> list[tuple[str, float]]:
    """Trim top-N lists by budget tier."""
    limit = {"compact": 3, "standard": 5, "full": 10}.get(tier, 5)
    return [(k, round(v / 3600.0, 2)) for k, v in items[:limit]]


def build_day_packet(
    day: DaySummary,
    tier: str = "standard",
) -> DayPacket:
    return DayPacket(
        meta=_meta(tier),
        date=day.date.isoformat(),
        active_hours=round(day.active_seconds / 3600.0, 2),
        recovery_hours=round(day.recovery_seconds / 3600.0, 2),
        dominant_mode=day.dominant_mode,
        dominant_project=day.dominant_project,
        dominant_topic=day.dominant_topic,
        chain_count=day.chain_count,
        signal_count=day.signal_count,
        command_count=day.command_count,
        transcript_count=day.transcript_count,
        commit_count=day.commit_count,
        top_modes=_top_n(day.top_modes, tier),
        top_projects=_top_n(day.top_projects, tier),
        top_topics=_top_n(day.top_topics, tier),
        highlights=list(day.highlights),
    )


def build_week_packet(
    week: WeekSummary,
    tier: str = "standard",
) -> WeekPacket:
    return WeekPacket(
        meta=_meta(tier),
        iso_week=week.iso_week,
        start_date=week.start_date.isoformat(),
        end_date=week.end_date.isoformat(),
        active_hours=round(week.active_seconds / 3600.0, 2),
        recovery_hours=round(week.recovery_seconds / 3600.0, 2),
        dominant_mode=week.dominant_mode,
        dominant_project=week.dominant_project,
        dominant_topic=week.dominant_topic,
        day_pattern=week.day_pattern,
        chain_count=week.chain_count,
        top_modes=_top_n(week.top_modes, tier),
        top_projects=_top_n(week.top_projects, tier),
        top_topics=_top_n(week.top_topics, tier),
        active_delta_vs_prior=round(week.active_delta_vs_prior / 3600.0, 2) if week.active_delta_vs_prior is not None else None,
    )


def build_month_packet(
    month_model: MonthSummary,
    tier: str = "standard",
) -> MonthPacket:
    return MonthPacket(
        meta=_meta(tier),
        month=month_model.month,
        active_hours=round(month_model.active_seconds / 3600.0, 2),
        recovery_hours=round(month_model.recovery_seconds / 3600.0, 2),
        active_days=month_model.active_days,
        chain_count=month_model.chain_count,
        signal_count=month_model.signal_count,
        dominant_modes=_top_n(month_model.top_modes, tier),
        dominant_projects=_top_n(month_model.top_projects, tier),
        dominant_topics=_top_n(month_model.top_topics, tier),
        highlights=list(month_model.highlights),
        chat_session_count=month_model.chat_session_count,
        chat_work_events=dict(month_model.chat_work_events),
        chat_cost_usd=month_model.chat_cost_usd,
        episode_count=month_model.episode_count,
        episode_labels=list(month_model.episode_labels),
    )


def build_episode_packet(
    episode: EpisodeSummary,
    tier: str = "standard",
) -> EpisodePacket:
    return EpisodePacket(
        meta=_meta(tier),
        episode_id=episode.episode_id,
        label=episode.label,
        start_date=episode.start_date.isoformat(),
        end_date=episode.end_date.isoformat(),
        days=episode.days,
        active_hours=round(episode.active_seconds / 3600.0, 2),
        dominant_mode=episode.dominant_mode,
        dominant_project=episode.dominant_project,
        dominant_topic=episode.dominant_topic,
        trigger=episode.trigger,
        confidence=episode.confidence,
    )


def build_project_packet(
    project: str,
    days: Sequence[DaySummary],
    chains: Sequence[ChainSummary],
    tier: str = "standard",
) -> ProjectPacket:
    total_seconds = 0.0
    day_count = 0
    mode_counter: Counter[str] = Counter()
    chain_ids: set[str] = set()

    for day in days:
        for proj, seconds in day.top_projects:
            if proj == project:
                total_seconds += seconds
                day_count += 1
                break
    for chain in chains:
        if chain.project == project:
            chain_ids.add(chain.chain_id)
            mode_counter[chain.mode] += chain.duration_seconds

    top_modes_raw = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
    return ProjectPacket(
        meta=_meta(tier),
        project=project,
        total_hours=round(total_seconds / 3600.0, 2),
        day_count=day_count,
        chain_count=len(chain_ids),
        top_modes=[(m, round(s / 3600.0, 2)) for m, s in top_modes_raw],
    )


def build_thread_packets(
    signals,
    *,
    n: int = 5,
    tier: str = "standard",
) -> list[ThreadPacket]:
    """Build thread packets from polylogue session signals grouped by thread_id."""
    from collections import defaultdict
    from datetime import datetime, timezone

    threads: dict[str, dict] = defaultdict(lambda: {
        "session_ids": set(),
        "start": None,
        "end": None,
        "projects": Counter(),
        "work_events": Counter(),
        "cost": 0.0,
        "depth": 0,
    })

    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        ev = signal.evidence if isinstance(signal.evidence, dict) else {}
        tid = ev.get("thread_id")
        if not tid:
            # Sessions without thread_id are their own single-session thread
            tid = ev.get("conversation_id", signal.signal_id)
        tid = str(tid)
        t = threads[tid]
        conv_id = ev.get("conversation_id", signal.signal_id)
        t["session_ids"].add(str(conv_id))
        t["start"] = min(t["start"], signal.start) if t["start"] else signal.start
        t["end"] = max(t["end"], signal.end) if t["end"] else signal.end
        proj = ev.get("project_hint") or ev.get("canonical_project")
        if proj:
            t["projects"][str(proj)] += 1
        we_kind = ev.get("work_event_kind")
        if we_kind:
            t["work_events"][str(we_kind)] += 1
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            t["cost"] += cost
        depth = ev.get("continuation_depth", 0)
        if isinstance(depth, int):
            t["depth"] = max(t["depth"], depth)

    now_str = datetime.now(timezone.utc).isoformat()
    packets = []
    sorted_threads = sorted(threads.items(), key=lambda item: (item[1]["end"] or datetime.min), reverse=True)
    for tid, t in sorted_threads[:n]:
        dominant_proj = t["projects"].most_common(1)[0][0] if t["projects"] else None
        start = t["start"] or datetime.now(timezone.utc)
        end = t["end"] or datetime.now(timezone.utc)
        packets.append(ThreadPacket(
            meta=ContextPacketMeta(schema=_SCHEMA_VERSION, generated_at=now_str, budget_tier=tier),
            thread_id=tid,
            depth=t["depth"],
            session_count=len(t["session_ids"]),
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            dominant_project=dominant_proj,
            work_event_breakdown=dict(t["work_events"]),
            total_cost_usd=t["cost"],
        ))
    return packets


def build_coverage_packet(
    days: Sequence[DaySummary],
    tier: str = "standard",
    anomaly_count: int = 0,
) -> CoveragePacket:
    source_counter: Counter[str] = Counter()
    total_signals = 0
    total_chains = 0
    aw_days = 0
    term_days = 0
    chat_days = 0
    git_days = 0

    for day in days:
        total_signals += day.signal_count
        total_chains += day.chain_count
        source_counter.update(day.source_counts)
        if day.coverage.get("has_activitywatch"):
            aw_days += 1
        if day.coverage.get("has_terminal"):
            term_days += 1
        if day.coverage.get("has_chatlog"):
            chat_days += 1
        if day.coverage.get("has_git"):
            git_days += 1

    return CoveragePacket(
        meta=_meta(tier),
        day_count=len(days),
        signal_count=total_signals,
        chain_count=total_chains,
        source_breakdown=dict(source_counter),
        days_with_activitywatch=aw_days,
        days_with_terminal=term_days,
        days_with_chatlog=chat_days,
        days_with_git=git_days,
        anomaly_count=anomaly_count,
    )


def build_quarter_packet(
    quarter: QuarterSummary,
    tier: str = "standard",
) -> QuarterPacket:
    return QuarterPacket(
        meta=_meta(tier),
        quarter=quarter.quarter,
        active_hours=round(quarter.active_seconds / 3600.0, 2),
        recovery_hours=round(quarter.recovery_seconds / 3600.0, 2),
        active_days=quarter.active_days,
        chain_count=quarter.chain_count,
        signal_count=quarter.signal_count,
        dominant_mode=quarter.dominant_mode,
        dominant_project=quarter.dominant_project,
        dominant_topic=quarter.dominant_topic,
        top_modes=_top_n(quarter.top_modes, tier),
        top_projects=_top_n(quarter.top_projects, tier),
        top_topics=_top_n(quarter.top_topics, tier),
        chat_session_count=quarter.chat_session_count,
        chat_cost_usd=quarter.chat_cost_usd,
        episode_count=quarter.episode_count,
        month_count=quarter.month_count,
        month_active_trend=[round(s / 3600.0, 2) for s in quarter.month_active_trend],
        active_delta_vs_prior=round(quarter.active_delta_vs_prior / 3600.0, 2) if quarter.active_delta_vs_prior is not None else None,
    )


def build_year_packet(
    year_model: YearSummary,
    tier: str = "standard",
) -> YearPacket:
    return YearPacket(
        meta=_meta(tier),
        year=year_model.year,
        active_hours=round(year_model.active_seconds / 3600.0, 2),
        recovery_hours=round(year_model.recovery_seconds / 3600.0, 2),
        active_days=year_model.active_days,
        chain_count=year_model.chain_count,
        signal_count=year_model.signal_count,
        dominant_mode=year_model.dominant_mode,
        dominant_project=year_model.dominant_project,
        dominant_topic=year_model.dominant_topic,
        top_modes=_top_n(year_model.top_modes, tier),
        top_projects=_top_n(year_model.top_projects, tier),
        top_topics=_top_n(year_model.top_topics, tier),
        chat_session_count=year_model.chat_session_count,
        chat_cost_usd=year_model.chat_cost_usd,
        episode_count=year_model.episode_count,
        quarter_count=year_model.quarter_count,
        quarter_active_trend=[round(s / 3600.0, 2) for s in year_model.quarter_active_trend],
        active_delta_vs_prior=round(year_model.active_delta_vs_prior / 3600.0, 2) if year_model.active_delta_vs_prior is not None else None,
    )


def build_theme_packets(months, weeks, tier: str = "standard") -> list[dict]:
    """Build theme packets from recurring projects and topics across months."""
    themes = detect_themes(months, weeks)
    return [
        ThemePacket(
            meta=_meta(tier),
            name=t.name,
            kind=t.kind,
            total_hours=t.total_hours,
            month_count=t.month_count,
            trend=t.trend,
            first_seen=t.first_seen,
            last_seen=t.last_seen,
        ).to_dict()
        for t in themes
    ]


def build_claims_packet(months, weeks, days, tier: str = "standard") -> dict:
    """Build claims packet from quantified activity patterns."""
    claims = generate_claims(months, weeks, days)
    return ClaimsPacket(
        meta=_meta(tier),
        claims=tuple(
            ClaimPacket(
                statement=c.statement,
                confidence=c.confidence,
                evidence_refs=c.evidence_refs,
                category=c.category,
            )
            for c in claims
        ),
    ).to_dict()


def build_project_arc_packets(months, weeks, episodes, tier: str = "standard") -> list[dict]:
    """Build project arc packets tracking velocity and momentum per top-5 project."""
    arcs = build_project_arcs(months, weeks, episodes)
    return [
        ProjectArcPacket(
            meta=_meta(tier),
            project=a.project,
            total_hours=a.total_hours,
            active_months=a.active_months,
            velocity_trend=a.velocity_trend,
            cost_usd=a.cost_usd,
            active_episodes=a.active_episodes,
            momentum=a.momentum,
        ).to_dict()
        for a in arcs
    ]


def _resolve_window_bounds(
    *,
    conn,
    days: int,
    end: Optional[datetime],
) -> tuple[date, date]:
    if end is not None:
        anchor = end.astimezone(timezone.utc).date() if end.tzinfo else end.date()
    else:
        anchor = _latest_observed_date(conn) or datetime.now(timezone.utc).date()
    return anchor - timedelta(days=max(days - 1, 0)), anchor


def _latest_observed_date(conn) -> date | None:
    candidates = (
        ("processed_delivery_telemetry", "date"),
        ("processed_git_daily", "date"),
        ("processed_focus_spans", "date"),
        ("processed_project_attention", "date"),
        ("processed_chat_activity", "date"),
        ("polylogue_session_profile", "last_message_at"),
    )
    anchors: list[date] = []
    for table_name, column_name in candidates:
        try:
            row = conn.execute(f"SELECT MAX({column_name}) FROM {table_name}").fetchone()
        except Exception:
            continue
        if not row or row[0] is None:
            continue
        raw = row[0]
        if isinstance(raw, datetime):
            anchors.append(raw.date())
        elif isinstance(raw, date):
            anchors.append(raw)
        else:
            text = str(raw).strip()
            if not text:
                continue
            try:
                anchors.append(datetime.fromisoformat(text.replace("Z", "+00:00")).date())
            except ValueError:
                try:
                    anchors.append(date.fromisoformat(text[:10]))
                except ValueError:
                    continue
    return max(anchors) if anchors else None


def _decode_json(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return None
    return json.loads(text)


def _list_from_json(value: object) -> list[Any]:
    parsed = _decode_json(value)
    return parsed if isinstance(parsed, list) else []


def _minutes_pairs_to_seconds(values: Sequence[tuple[str, Any]]) -> tuple[tuple[str, float], ...]:
    return tuple((str(label), float(value or 0.0) * 60.0) for label, value in values)


def _seconds_pairs(values: Sequence[tuple[str, Any]]) -> tuple[tuple[str, float], ...]:
    return tuple((str(label), float(value or 0.0)) for label, value in values)


def _top_label(values: Sequence[tuple[str, Any]]) -> str | None:
    if not values:
        return None
    label, _ = values[0]
    return str(label)


def _aggregate_pairs(days: Sequence[DaySummary], attribute: str, *, limit: int = 5) -> tuple[tuple[str, float], ...]:
    counter: Counter[str] = Counter()
    for day in days:
        counter.update({name: seconds for name, seconds in getattr(day, attribute)})
    return tuple((name, float(value)) for name, value in counter.most_common(limit))


def _aggregate_source_counts(days: Sequence[DaySummary]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for day in days:
        counter.update(day.source_counts)
    return dict(counter)


def _aggregate_highlights(days: Sequence[DaySummary], *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    highlights: list[str] = []
    for day in days:
        for highlight in day.highlights:
            if highlight in seen:
                continue
            seen.add(highlight)
            highlights.append(highlight)
            if len(highlights) >= limit:
                return highlights
    return highlights


def _classify_week_pattern(days: Sequence[DaySummary]) -> str:
    if not days:
        return "unknown"
    weekday_hours = [day.active_seconds / 3600.0 for day in days if day.date.weekday() < 5]
    weekend_hours = [day.active_seconds / 3600.0 for day in days if day.date.weekday() >= 5]
    if weekday_hours and weekend_hours:
        weekday_avg = sum(weekday_hours) / len(weekday_hours)
        weekend_avg = sum(weekend_hours) / len(weekend_hours)
        if weekday_avg > weekend_avg * 1.3:
            return "weekday-heavy"
        if weekend_avg > weekday_avg * 1.2:
            return "weekend-heavy"
        return "uniform"
    return "weekday-heavy" if weekday_hours else "weekend-heavy"


def _week_summary_from_days(key: str, days: Sequence[DaySummary]) -> WeekSummary:
    period = parse_period("week", key)
    if period is None:
        raise ValueError(f"Invalid week key: {key}")
    top_modes = _aggregate_pairs(days, "top_modes")
    top_projects = _aggregate_pairs(days, "top_projects")
    top_topics = _aggregate_pairs(days, "top_topics")
    busiest_day = max(days, key=lambda day: day.active_seconds).date if days else None
    quietest_day = min(days, key=lambda day: day.active_seconds).date if days else None
    return WeekSummary(
        iso_week=key,
        start_date=period.start,
        end_date=period.end,
        days=len(days),
        active_seconds=sum(day.active_seconds for day in days),
        recovery_seconds=sum(day.recovery_seconds for day in days),
        chain_count=sum(day.chain_count for day in days),
        signal_count=sum(day.signal_count for day in days),
        command_count=sum(day.command_count for day in days),
        transcript_count=sum(day.transcript_count for day in days),
        commit_count=sum(day.commit_count for day in days),
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        day_pattern=_classify_week_pattern(days),
        busiest_day=busiest_day,
        quietest_day=quietest_day,
        active_delta_vs_prior=None,
        dominant_mode=_top_label(top_modes),
        dominant_project=_top_label(top_projects),
        dominant_topic=_top_label(top_topics),
    )


def _month_summary_from_days(key: str, days: Sequence[DaySummary], weeks: Sequence[WeekSummary], episodes: Sequence[EpisodeSummary]) -> MonthSummary:
    period = parse_period("month", key)
    if period is None:
        raise ValueError(f"Invalid month key: {key}")
    top_modes = _aggregate_pairs(days, "top_modes")
    top_projects = _aggregate_pairs(days, "top_projects")
    top_topics = _aggregate_pairs(days, "top_topics")
    coverage_summary = {
        "days_with_activitywatch": sum(1 for day in days if day.coverage.get("has_activitywatch")),
        "days_with_terminal": sum(1 for day in days if day.coverage.get("has_terminal")),
        "days_with_chatlog": sum(1 for day in days if day.coverage.get("has_chatlog")),
        "days_with_git": sum(1 for day in days if day.coverage.get("has_git")),
    }
    chat_work_events: Counter[str] = Counter()
    for day in days:
        chat_work_events.update(day.chat_work_events)
    month_episodes = [
        episode
        for episode in episodes
        if episode.end_date >= period.start and episode.start_date <= period.end
    ]
    return MonthSummary(
        month=key,
        start_date=period.start,
        end_date=period.end,
        total_days=(period.end - period.start).days + 1,
        active_days=sum(1 for day in days if day.active_seconds > 0),
        active_seconds=sum(day.active_seconds for day in days),
        recovery_seconds=sum(day.recovery_seconds for day in days),
        chain_count=sum(day.chain_count for day in days),
        signal_count=sum(day.signal_count for day in days),
        command_count=sum(day.command_count for day in days),
        transcript_count=sum(day.transcript_count for day in days),
        commit_count=sum(day.commit_count for day in days),
        dominant_mode=_top_label(top_modes),
        dominant_project=_top_label(top_projects),
        dominant_topic=_top_label(top_topics),
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        source_counts=_aggregate_source_counts(days),
        coverage_summary=coverage_summary,
        highlights=_aggregate_highlights(days),
        chat_session_count=sum(day.chat_session_count for day in days),
        chat_work_events=dict(chat_work_events),
        chat_cost_usd=round(sum(day.chat_cost_usd for day in days), 4),
        episode_count=len(month_episodes),
        episode_labels=[episode.label for episode in month_episodes[:8]],
        week_count=len(weeks),
        day_patterns=sorted({week.day_pattern for week in weeks if week.day_pattern}),
        active_delta_vs_prior=None,
    )


def _quarter_summary_from_months(key: str, months: Sequence[MonthSummary]) -> QuarterSummary:
    top_modes_counter: Counter[str] = Counter()
    top_projects_counter: Counter[str] = Counter()
    top_topics_counter: Counter[str] = Counter()
    for month in months:
        top_modes_counter.update({name: seconds for name, seconds in month.top_modes})
        top_projects_counter.update({name: seconds for name, seconds in month.top_projects})
        top_topics_counter.update({name: seconds for name, seconds in month.top_topics})
    top_modes = tuple((name, float(value)) for name, value in top_modes_counter.most_common(5))
    top_projects = tuple((name, float(value)) for name, value in top_projects_counter.most_common(5))
    top_topics = tuple((name, float(value)) for name, value in top_topics_counter.most_common(5))
    return QuarterSummary(
        quarter=key,
        active_seconds=sum(month.active_seconds for month in months),
        recovery_seconds=sum(month.recovery_seconds for month in months),
        active_days=sum(month.active_days for month in months),
        chain_count=sum(month.chain_count for month in months),
        signal_count=sum(month.signal_count for month in months),
        dominant_mode=_top_label(top_modes),
        dominant_project=_top_label(top_projects),
        dominant_topic=_top_label(top_topics),
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        chat_session_count=sum(month.chat_session_count for month in months),
        chat_cost_usd=round(sum(month.chat_cost_usd for month in months), 4),
        episode_count=sum(month.episode_count for month in months),
        month_count=len(months),
        month_active_trend=[month.active_seconds for month in months],
        active_delta_vs_prior=None,
    )


def _year_summary_from_quarters(key: str, quarters: Sequence[QuarterSummary]) -> YearSummary:
    top_modes_counter: Counter[str] = Counter()
    top_projects_counter: Counter[str] = Counter()
    top_topics_counter: Counter[str] = Counter()
    for quarter in quarters:
        top_modes_counter.update({name: seconds for name, seconds in quarter.top_modes})
        top_projects_counter.update({name: seconds for name, seconds in quarter.top_projects})
        top_topics_counter.update({name: seconds for name, seconds in quarter.top_topics})
    top_modes = tuple((name, float(value)) for name, value in top_modes_counter.most_common(5))
    top_projects = tuple((name, float(value)) for name, value in top_projects_counter.most_common(5))
    top_topics = tuple((name, float(value)) for name, value in top_topics_counter.most_common(5))
    return YearSummary(
        year=key,
        active_seconds=sum(quarter.active_seconds for quarter in quarters),
        recovery_seconds=sum(quarter.recovery_seconds for quarter in quarters),
        active_days=sum(quarter.active_days for quarter in quarters),
        chain_count=sum(quarter.chain_count for quarter in quarters),
        signal_count=sum(quarter.signal_count for quarter in quarters),
        dominant_mode=_top_label(top_modes),
        dominant_project=_top_label(top_projects),
        dominant_topic=_top_label(top_topics),
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        chat_session_count=sum(quarter.chat_session_count for quarter in quarters),
        chat_cost_usd=round(sum(quarter.chat_cost_usd for quarter in quarters), 4),
        episode_count=sum(quarter.episode_count for quarter in quarters),
        quarter_count=len(quarters),
        quarter_active_trend=[quarter.active_seconds for quarter in quarters],
        active_delta_vs_prior=None,
    )


def _apply_active_deltas(models: Sequence[Any]) -> None:
    prior_active_seconds: float | None = None
    for model in models:
        delta = None if prior_active_seconds is None else model.active_seconds - prior_active_seconds
        object.__setattr__(model, "active_delta_vs_prior", delta)
        prior_active_seconds = model.active_seconds


def _period_packet_from_days(day_models: Sequence[DaySummary], *, start: date, end: date) -> dict[str, Any]:
    if not day_models:
        return {}

    active_seconds = sum(day.active_seconds for day in day_models)
    recovery_seconds = sum(day.recovery_seconds for day in day_models)
    command_count = sum(day.command_count for day in day_models)
    transcript_count = sum(day.transcript_count for day in day_models)
    commit_count = sum(day.commit_count for day in day_models)
    signal_count = sum(day.signal_count for day in day_models)
    chain_count = sum(day.chain_count for day in day_models)
    top_mode_counter: Counter[str] = Counter()
    top_project_counter: Counter[str] = Counter()
    top_topic_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    highlights: list[str] = []
    coverage = {
        "days_with_activitywatch": 0,
        "days_with_terminal": 0,
        "days_with_chatlog": 0,
        "days_with_git": 0,
    }

    for day in day_models:
        top_mode_counter.update({name: seconds for name, seconds in day.top_modes})
        top_project_counter.update({name: seconds for name, seconds in day.top_projects})
        top_topic_counter.update({name: seconds for name, seconds in day.top_topics})
        source_counter.update(day.source_counts)
        highlights.extend(day.highlights[:2])
        day_coverage = day.coverage
        if day_coverage.get("has_activitywatch"):
            coverage["days_with_activitywatch"] += 1
        if day_coverage.get("has_terminal"):
            coverage["days_with_terminal"] += 1
        if day_coverage.get("has_chatlog"):
            coverage["days_with_chatlog"] += 1
        if day_coverage.get("has_git"):
            coverage["days_with_git"] += 1

    top_modes = tuple(top_mode_counter.most_common(5))
    top_projects = tuple(top_project_counter.most_common(5))
    top_topics = tuple(top_topic_counter.most_common(5))
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_days": len(day_models),
        "active_seconds": active_seconds,
        "recovery_seconds": recovery_seconds,
        "active_hours": round(active_seconds / 3600.0, 2),
        "recovery_hours": round(recovery_seconds / 3600.0, 2),
        "chain_count": chain_count,
        "signal_count": signal_count,
        "command_count": command_count,
        "transcript_count": transcript_count,
        "commit_count": commit_count,
        "top_modes": _top_n(top_modes, "full"),
        "top_projects": _top_n(top_projects, "full"),
        "top_topics": _top_n(top_topics, "full"),
        "source_counts": dict(source_counter),
        "coverage": coverage,
        "highlights": highlights[:12],
    }


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_thread_packets_from_profiles(
    profile_rows: Sequence[dict[str, Any]],
    *,
    n: int = 5,
    tier: str = "standard",
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in profile_rows:
        thread_key = str(row.get("thread_id") or row.get("conversation_id"))
        entry = grouped.setdefault(
            thread_key,
            {
                "conversation_ids": set(),
                "start": None,
                "end": None,
                "project_counts": Counter(),
                "kind_counts": Counter(),
                "cost": 0.0,
                "depth": 0,
            },
        )
        conversation_id = row.get("conversation_id")
        if conversation_id:
            entry["conversation_ids"].add(str(conversation_id))
        start = _coerce_datetime(row.get("first_message_at") or row.get("created_at"))
        end = _coerce_datetime(row.get("last_message_at") or row.get("created_at"))
        if start is not None and (entry["start"] is None or start < entry["start"]):
            entry["start"] = start
        if end is not None and (entry["end"] is None or end > entry["end"]):
            entry["end"] = end
        for project in _list_from_json(row.get("canonical_projects_json")):
            entry["project_counts"][str(project)] += 1
        dominant_kind = row.get("dominant_work_kind")
        if dominant_kind:
            entry["kind_counts"][str(dominant_kind)] += int(row.get("work_event_count") or 1)
        entry["cost"] += float(row.get("cost_usd") or 0.0)
        depth = row.get("continuation_depth")
        if isinstance(depth, int):
            entry["depth"] = max(entry["depth"], depth)

    packets: list[dict[str, object]] = []
    for thread_id, entry in sorted(
        grouped.items(),
        key=lambda item: item[1]["end"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:n]:
        packet = ThreadPacket(
            meta=_meta(tier),
            thread_id=thread_id,
            depth=entry["depth"],
            session_count=len(entry["conversation_ids"]),
            start_date=(entry["start"] or datetime.now(timezone.utc)).date().isoformat(),
            end_date=(entry["end"] or datetime.now(timezone.utc)).date().isoformat(),
            dominant_project=entry["project_counts"].most_common(1)[0][0] if entry["project_counts"] else None,
            work_event_breakdown=dict(entry["kind_counts"]),
            total_cost_usd=entry["cost"],
        )
        packets.append(packet.to_dict())
    return packets


def _aggregate_chat_work_events_from_profiles(profile_rows: Sequence[dict[str, Any]]) -> dict[str, object]:
    session_ids = {str(row["conversation_id"]) for row in profile_rows if row.get("conversation_id")}
    work_event_counter: Counter[str] = Counter()
    total_cost = 0.0
    for row in profile_rows:
        kind = row.get("dominant_work_kind")
        if kind:
            work_event_counter[str(kind)] += int(row.get("work_event_count") or 1)
        total_cost += float(row.get("cost_usd") or 0.0)
    return {
        "session_count": len(session_ids),
        "work_event_breakdown": dict(work_event_counter),
        "total_cost_usd": round(total_cost, 4),
    }


def build_current_state(
    *,
    days: int = 14,
    end: Optional[datetime] = None,
    tier: str = "standard",
) -> dict[str, object]:
    """Build a complete current-state packet from shared evidence queries."""
    conn = open_warehouse_read_only()
    try:
        window_start, window_end = _resolve_window_bounds(conn=conn, days=days, end=end)
        evidence_queries = query_evidence_range(
            conn,
            start=window_start,
            end=window_end,
            artifact_limits=False,
        )
        freshness = [row.to_dict() for row in inspect_core_surface_freshness(conn=conn, reference_date=window_end)]
    finally:
        conn.close()

    grouped_rows = group_query_rows_by_day(evidence_queries)
    day_models: list[DaySummary] = []
    current_day = window_start
    while current_day <= window_end:
        day_bundle = bundle_for_day(
            target=current_day,
            queries=evidence_queries,
            grouped_rows=grouped_rows,
            freshness=freshness,
        )
        day_models.append(day_summary_from_summary(current_day, summarize_evidence_surfaces(day_bundle)))
        current_day += timedelta(days=1)

    anomaly_models = detect_anomalies(day_models)
    episode_models = detect_episodes(day_models, anomalies=anomaly_models)
    weeks_by_key: dict[str, list[DaySummary]] = {}
    for day in day_models:
        weeks_by_key.setdefault(key_for_date("week", day.date), []).append(day)
    week_models = [
        _week_summary_from_days(key, weeks_by_key.get(key, ()))
        for key in period_keys_in_range("week", window_start, window_end)
    ]
    _apply_active_deltas(week_models)

    months_by_key: dict[str, list[DaySummary]] = {}
    for day in day_models:
        months_by_key.setdefault(key_for_date("month", day.date), []).append(day)
    month_models: list[MonthSummary] = []
    for key in period_keys_in_range("month", window_start, window_end):
        period = parse_period("month", key)
        if period is None:
            continue
        month_weeks = [
            week
            for week in week_models
            if week.end_date >= period.start and week.start_date <= period.end
        ]
        month_models.append(
            _month_summary_from_days(
                key,
                months_by_key.get(key, ()),
                month_weeks,
                episode_models,
            )
        )
    _apply_active_deltas(month_models)

    quarters_by_key: dict[str, list[MonthSummary]] = {}
    for month in month_models:
        quarters_by_key.setdefault(key_for_date("quarter", month.start_date), []).append(month)
    quarter_models = [
        _quarter_summary_from_months(key, quarters_by_key.get(key, ()))
        for key in period_keys_in_range("quarter", window_start, window_end)
    ]
    _apply_active_deltas(quarter_models)

    years_by_key: dict[str, list[QuarterSummary]] = {}
    for quarter in quarter_models:
        period = parse_period("quarter", quarter.quarter)
        if period is None:
            continue
        years_by_key.setdefault(key_for_date("year", period.start), []).append(quarter)
    year_models = [
        _year_summary_from_quarters(key, years_by_key.get(key, ()))
        for key in period_keys_in_range("year", window_start, window_end)
    ]
    _apply_active_deltas(year_models)

    day_limit = {"compact": 3, "standard": 7, "full": days}.get(tier, 7)
    day_packets = [build_day_packet(day, tier).to_dict() for day in day_models[-day_limit:]]
    week_packets = [build_week_packet(week, tier).to_dict() for week in week_models[-4:]]
    month_packets = [build_month_packet(month, tier).to_dict() for month in month_models[-3:]]
    quarter_packets = [build_quarter_packet(quarter, tier).to_dict() for quarter in quarter_models[-4:]]
    year_packets = [build_year_packet(year_model, tier).to_dict() for year_model in year_models[-2:]]
    episode_packets = [build_episode_packet(episode, tier).to_dict() for episode in episode_models[-5:]]
    anomaly_packets = [anomaly.to_dict() for anomaly in anomaly_models[-12:]]
    coverage_packet = build_coverage_packet(day_models, tier, anomaly_count=len(anomaly_models)).to_dict()
    profile_rows = next((query.rows for query in evidence_queries if query.query_id == "polylogue_sessions"), [])
    chat_work_events = _aggregate_chat_work_events_from_profiles(profile_rows)
    thread_packets = _build_thread_packets_from_profiles(profile_rows, n=5, tier=tier)
    focus_loop_rows = next((query.rows for query in evidence_queries if query.query_id == "focus_loops"), [])
    recent_focus_loops = build_recent_focus_loops(focus_loop_rows, limit=15)
    period = _period_packet_from_days(day_models, start=window_start, end=window_end)

    theme_packets = build_theme_packets(month_models, week_models, tier=tier)
    memory_store = load_memory()
    if not theme_packets and memory_store.themes:
        theme_packets = [
            {
                "name": t.name,
                "kind": t.kind,
                "total_hours": t.total_hours,
                "trend": t.trend,
                "first_seen": t.first_seen,
                "last_seen": t.last_seen,
                "months_active": t.months_active,
                "source": "memory",
            }
            for t in sorted(memory_store.themes, key=lambda t: -t.total_hours)
        ]

    claims_packet = build_claims_packet(month_models, week_models, day_models, tier=tier)
    project_arc_packets = build_project_arc_packets(month_models, week_models, episode_models, tier=tier)
    memory_packet = build_memory_packet(memory_store)

    return {
        "schema": _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget_tier": tier,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": days,
        },
        "freshness": freshness,
        "coverage": coverage_packet,
        "period": period,
        "current": build_day_packet(day_models[-1], tier).to_dict() if day_models else None,
        "days": day_packets,
        "weeks": week_packets,
        "months": month_packets,
        "quarters": quarter_packets,
        "years": year_packets,
        "episodes": episode_packets,
        "anomalies": anomaly_packets,
        "recent_focus_loops": recent_focus_loops,
        "chat_work_events": chat_work_events,
        "threads": thread_packets,
        "themes": theme_packets,
        "claims": claims_packet,
        "project_arcs": project_arc_packets,
        "memory": memory_packet,
    }


def _aggregate_chat_work_events(signals) -> dict[str, object]:
    """Extract work event metadata from polylogue session signals."""
    work_event_counter: Counter[str] = Counter()
    session_ids: set[str] = set()
    total_cost = 0.0
    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        ev = signal.evidence
        conv_id = ev.get("conversation_id")
        if conv_id:
            session_ids.add(str(conv_id))
        kind = ev.get("work_event_kind")
        if kind:
            work_event_counter[str(kind)] += 1
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += cost
    return {
        "session_count": len(session_ids),
        "work_event_breakdown": dict(work_event_counter),
        "total_cost_usd": round(total_cost, 4),
    }


def _chain_packet(chain) -> dict[str, object]:
    return {
        "chain_id": chain.chain_id,
        "start": chain.start.isoformat(),
        "end": chain.end.isoformat(),
        "duration_minutes": round(chain.duration_seconds / 60.0, 2),
        "mode": chain.mode,
        "project": chain.project,
        "topic": chain.topic,
        "sources": list(chain.sources),
        "apps": list(chain.apps),
        "domains": list(chain.domains),
        "titles": list(chain.titles[:3]),
        "reasons": list(chain.reasons),
        "quality_flags": list(chain.quality_flags),
    }
