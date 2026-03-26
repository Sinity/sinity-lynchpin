"""Shared day-level rollup helpers for evidence-derived context surfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Sequence

from ..periods import parse_period
from .bundles import EvidenceBundle, EvidenceQuery
from .summary_models import DaySummary


def row_primary_date(row: dict[str, Any]) -> date | None:
    for column in ("date", "start", "authored_at", "last_message_at", "first_message_at", "created_at"):
        value = row.get(column)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                continue
    return None


def group_query_rows_by_day(queries: Sequence[EvidenceQuery]) -> dict[str, dict[date, list[dict[str, Any]]]]:
    grouped: dict[str, dict[date, list[dict[str, Any]]]] = {}
    for query in queries:
        by_day: dict[date, list[dict[str, Any]]] = {}
        for row in query.rows:
            day = row_primary_date(row)
            if day is None:
                continue
            by_day.setdefault(day, []).append(row)
        grouped[query.query_id] = by_day
    return grouped


def bundle_for_day(
    *,
    target: date,
    queries: Sequence[EvidenceQuery],
    grouped_rows: dict[str, dict[date, list[dict[str, Any]]]],
    freshness: Sequence[Any],
) -> EvidenceBundle:
    return EvidenceBundle(
        period=parse_period("day", target.isoformat()),
        generated_at=datetime.now(timezone.utc).isoformat(),
        freshness=list(freshness),
        queries=[
            EvidenceQuery(
                query_id=query.query_id,
                title=query.title,
                sql=query.sql,
                params=list(query.params),
                rows=list(grouped_rows.get(query.query_id, {}).get(target, [])),
                error=query.error,
            )
            for query in queries
        ],
        notes=[],
        bundle_ref=None,
    )


def day_summary_from_summary(target: date, summary: dict[str, Any]) -> DaySummary:
    active_seconds = float(summary["delivery"]["active_hours"] or 0.0) * 3600.0
    recovery_seconds = float(summary["circadian"]["recovery_minutes_total"] or 0.0) * 60.0
    top_modes = _minutes_pairs_to_seconds(summary["focus"]["top_modes"])
    focus_projects = _minutes_pairs_to_seconds(summary["focus"]["top_projects"])
    if focus_projects:
        top_projects = focus_projects
    else:
        top_projects = tuple(
            (str(project), active_seconds)
            for project, _count in summary["attention"]["top_projects"][:1]
            if project and active_seconds > 0
        )
    query_rows = summary["evidence"]["query_rows"]
    transcript_count = max(
        int(summary["delivery"]["chat_sessions"] or 0),
        sum(int(count or 0) for _provider, count in summary["chat"]["providers"]),
        len(summary["chat"]["top_session_titles"]),
    )
    commit_count = int(summary["delivery"]["total_commits"] or 0)
    if commit_count == 0:
        commit_count = sum(int(count or 0) for _repo, count in summary["git"]["repos"])
    source_counts = {str(name): int(count or 0) for name, count in query_rows.items()}
    has_activitywatch = bool(
        source_counts.get("focus_spans")
        or source_counts.get("focus_loops")
        or source_counts.get("context_switches")
        or source_counts.get("circadian")
    )
    has_terminal = bool(summary["delivery"]["command_count"])
    has_chatlog = bool(transcript_count or source_counts.get("polylogue_sessions"))
    has_git = bool(commit_count or source_counts.get("git_daily") or source_counts.get("git_file_facts"))
    dominant_mode = _top_label(summary["focus"]["top_modes"]) or _top_label(summary["circadian"]["dominant_modes"])
    dominant_project = (
        _top_label(summary["focus"]["top_projects"])
        or _top_label(summary["attention"]["top_projects"])
        or _top_label(summary["chat"]["top_session_projects"])
        or _top_label(summary["delivery"]["top_repos"])
    )

    return DaySummary(
        date=target,
        active_seconds=active_seconds,
        recovery_seconds=recovery_seconds,
        chain_count=int(source_counts.get("focus_loops", 0)),
        signal_count=sum(source_counts.values()),
        command_count=int(summary["delivery"]["command_count"] or 0),
        transcript_count=transcript_count,
        commit_count=commit_count,
        dominant_mode=dominant_mode,
        dominant_project=dominant_project,
        dominant_topic=None,
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=(),
        source_counts=source_counts,
        coverage={
            "has_activitywatch": has_activitywatch,
            "has_terminal": has_terminal,
            "has_chatlog": has_chatlog,
            "has_git": has_git,
        },
        highlights=_day_highlights(summary),
        projects=tuple(project for project, _seconds in top_projects),
        chat_session_count=int(summary["delivery"]["chat_sessions"] or transcript_count),
        chat_work_events={str(kind): int(count or 0) for kind, count in summary["chat"]["work_kinds"]},
        chat_cost_usd=float(summary["chat"].get("total_cost_usd") or 0.0),
    )


def _day_highlights(summary: dict[str, Any]) -> list[str]:
    highlights: list[str] = []
    top_repo = _top_label(summary["delivery"]["top_repos"])
    if top_repo:
        highlights.append(f"repo: {top_repo}")
    top_path = _top_label(summary["git"]["top_paths"])
    if top_path:
        highlights.append(f"path: {top_path}")
    session_title = _top_label(summary["chat"]["top_session_titles"])
    if session_title:
        highlights.append(f"session: {session_title}")
    top_loop = _top_label(summary["focus"]["top_loops"])
    if top_loop:
        highlights.append(f"focus loop: {top_loop}")
    return highlights


def _minutes_pairs_to_seconds(values: Sequence[tuple[str, Any]]) -> tuple[tuple[str, float], ...]:
    return tuple((str(label), float(value or 0.0) * 60.0) for label, value in values)


def _top_label(values: Sequence[tuple[str, Any]]) -> str | None:
    if not values:
        return None
    label, _ = values[0]
    return str(label)
