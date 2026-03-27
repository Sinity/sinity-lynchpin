"""Current-state assembly from evidence-window queries."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence, Optional

from .bundles import query_evidence_range
from .day_rollups import bundle_for_day, day_summary_from_summary, group_query_rows_by_day
from .memory import build_memory_packet, load_memory
from .packet_types import ThreadPacket
from .patterns import build_recent_focus_loops, detect_anomalies, detect_episodes
from .period_rollups import summarize_months, summarize_quarters, summarize_weeks, summarize_years
from .reports import summarize_evidence_surfaces
from .state_packets import (
    _meta,
    build_claims_packet,
    build_coverage_packet,
    build_day_packet,
    build_episode_packet,
    build_month_packet,
    build_project_arc_packets,
    build_quarter_packet,
    build_theme_packets,
    build_week_packet,
    build_year_packet,
)
from .summary_models import DaySummary
from .trust import inspect_core_surface_freshness, open_warehouse_read_only


def _resolve_window_bounds(*, conn, days: int, end: Optional[datetime]) -> tuple[date, date]:
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
        "top_modes": [(key, round(value / 3600.0, 2)) for key, value in top_modes[:10]],
        "top_projects": [(key, round(value / 3600.0, 2)) for key, value in top_projects[:10]],
        "top_topics": [(key, round(value / 3600.0, 2)) for key, value in top_topics[:10]],
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
    now = datetime.now(timezone.utc)
    for thread_id, entry in sorted(
        grouped.items(),
        key=lambda item: item[1]["end"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:n]:
        packets.append(
            ThreadPacket(
                meta=_meta(tier),
                thread_id=thread_id,
                depth=entry["depth"],
                session_count=len(entry["conversation_ids"]),
                start_date=(entry["start"] or now).date().isoformat(),
                end_date=(entry["end"] or now).date().isoformat(),
                dominant_project=entry["project_counts"].most_common(1)[0][0] if entry["project_counts"] else None,
                work_event_breakdown=dict(entry["kind_counts"]),
                total_cost_usd=entry["cost"],
            ).to_dict()
        )
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


def build_current_state(*, days: int = 14, end: Optional[datetime] = None, tier: str = "standard") -> dict[str, object]:
    conn = open_warehouse_read_only()
    try:
        window_start, window_end = _resolve_window_bounds(conn=conn, days=days, end=end)
        evidence_queries = query_evidence_range(conn, start=window_start, end=window_end, artifact_limits=False)
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
    week_models = summarize_weeks(day_models)
    month_models = summarize_months(day_models)
    quarter_models = summarize_quarters(month_models)
    year_models = summarize_years(quarter_models)

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
                "name": theme.name,
                "kind": theme.kind,
                "total_hours": theme.total_hours,
                "trend": theme.trend,
                "first_seen": theme.first_seen,
                "last_seen": theme.last_seen,
                "months_active": theme.months_active,
                "source": "memory",
            }
            for theme in sorted(memory_store.themes, key=lambda item: -item.total_hours)
        ]

    claims_packet = build_claims_packet(month_models, week_models, day_models, tier=tier)
    project_arc_packets = build_project_arc_packets(month_models, week_models, episode_models, tier=tier)
    memory_packet = build_memory_packet(memory_store)

    return {
        "schema": "lynchpin-context-state-v3",
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
