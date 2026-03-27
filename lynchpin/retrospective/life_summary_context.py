from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from ..context.reports import build_period_report
from .life_summary_models import LifeMonthContextSummary
from .life_summary_utils import _counter_mapping, _counter_pairs, _month_after, _month_start, _render_counter

RECENT_CONTEXT_LOOKBACK_DAYS = 62


def build_recent_context_summaries(
    months: Sequence[str],
    *,
    lookback_days: int = RECENT_CONTEXT_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> tuple[dict[str, LifeMonthContextSummary], dict[str, object]]:
    if not months:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    tz = datetime.now().astimezone().tzinfo or timezone.utc
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    recent_floor = (current - timedelta(days=lookback_days)).date().strftime("%Y-%m")
    target_months = [month for month in months if month >= recent_floor]
    if not target_months:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    start_dt = _month_start(min(target_months), tz)
    end_dt = min(_month_after(max(target_months), tz), current)
    if start_dt >= end_dt:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    reports = {
        month: build_period_report("month", month, output_root=None, write_files=False)
        for month in sorted(target_months)
    }

    return (
        {month: _report_to_context_summary(report) for month, report in reports.items()},
        {
            "lookback_days": lookback_days,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "month_count": len(reports),
            "source": "context-reports",
        },
    )


def _report_to_context_summary(report: Any) -> LifeMonthContextSummary:
    payload = report.payload if isinstance(getattr(report, "payload", None), Mapping) else {}
    period = payload.get("period") if isinstance(payload, Mapping) else {}
    summary = payload.get("summary") if isinstance(payload, Mapping) else {}
    evidence = summary.get("evidence") if isinstance(summary, Mapping) else {}
    delivery = summary.get("delivery") if isinstance(summary, Mapping) else {}
    focus = summary.get("focus") if isinstance(summary, Mapping) else {}
    chat = summary.get("chat") if isinstance(summary, Mapping) else {}
    git = summary.get("git") if isinstance(summary, Mapping) else {}
    circadian = summary.get("circadian") if isinstance(summary, Mapping) else {}
    patterns = summary.get("patterns") if isinstance(summary, Mapping) else {}
    if not isinstance(patterns, Mapping):
        patterns = {}
    query_rows = evidence.get("query_rows") if isinstance(evidence, Mapping) else {}
    if not isinstance(query_rows, Mapping):
        query_rows = {}
    highlights: list[str] = []
    if delivery.get("top_repos"):
        highlights.append(f"Repos: {_render_counter(delivery.get('top_repos') or [], limit=3)}")
    if git.get("top_paths"):
        highlights.append(f"Paths: {_render_counter(git.get('top_paths') or [], limit=3)}")
    if chat.get("top_session_titles"):
        highlights.append(f"Sessions: {_render_counter(chat.get('top_session_titles') or [], limit=3)}")
    if patterns.get("episode_count"):
        labels = patterns.get("episode_labels") or []
        highlights.append(f"Episodes: {int(patterns.get('episode_count') or 0)} ({', '.join(labels[:3]) or 'n/a'})")
    if patterns.get("anomaly_count"):
        kinds = patterns.get("anomaly_kinds") or []
        highlights.append(f"Anomalies: {int(patterns.get('anomaly_count') or 0)} ({', '.join(kinds[:3]) or 'n/a'})")
    return LifeMonthContextSummary(
        start_date=str(period.get("start") or ""),
        end_date=str(period.get("end") or ""),
        days=int(evidence.get("days_with_evidence") or evidence.get("period_days") or 0),
        active_hours=float(delivery.get("active_hours") or 0.0),
        recovery_hours=round(float(circadian.get("recovery_minutes_total") or 0.0) / 60.0, 2),
        chain_count=int(query_rows.get("focus_loops") or 0),
        signal_count=int(query_rows.get("focus_spans") or 0),
        command_count=int(delivery.get("command_count") or 0),
        transcript_count=int(query_rows.get("polylogue_sessions") or 0),
        commit_count=int(delivery.get("total_commits") or 0),
        dominant_modes=_counter_pairs(focus.get("top_modes") or circadian.get("dominant_modes") or [], divisor=60.0),
        dominant_projects=_counter_pairs(
            focus.get("top_projects") or circadian.get("dominant_projects") or [],
            divisor=60.0,
        ),
        dominant_topics=_counter_pairs(chat.get("work_kinds") or [], divisor=1.0),
        source_counts={str(key): int(value) for key, value in query_rows.items()},
        coverage={
            surface: {
                "present": surface in (evidence.get("surfaces_present") or []),
                "rows": int(query_rows.get(surface) or 0),
            }
            for surface in sorted(query_rows)
        },
        highlights=highlights,
        chat_session_count=int(delivery.get("chat_sessions") or 0),
        chat_work_events=_counter_mapping(chat.get("work_kinds") or []),
        chat_cost_usd=float(chat.get("total_cost_usd") or 0.0),
        episode_count=int(patterns.get("episode_count") or 0),
        episode_labels=[str(label) for label in (patterns.get("episode_labels") or [])],
        anomaly_count=int(patterns.get("anomaly_count") or 0),
        anomaly_kinds=[str(kind) for kind in (patterns.get("anomaly_kinds") or [])],
        top_repos=_counter_pairs(delivery.get("top_repos") or [], divisor=1.0),
        top_paths=_counter_pairs(git.get("top_paths") or [], divisor=1.0),
        top_session_titles=_counter_pairs(chat.get("top_session_titles") or [], divisor=1.0),
        avg_fragmentation=float(focus.get("avg_fragmentation")) if focus.get("avg_fragmentation") is not None else None,
        evidence_bundle=payload.get("bundle_ref"),
    )
