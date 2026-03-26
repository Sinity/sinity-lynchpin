"""Generic period reports over evidence bundles.

These reports replace the old calendar-specific dossier layer. They render
canonical period semantics and evidence bundles into reusable summaries.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ..core.dates import parse_iso_dateish
from ..core.io import write_text_if_changed
from ..periods import hierarchical_relpath, normalize_scale, period_keys_in_range, period_label
from .bundles import EvidenceBundle, EvidenceQuery, build_period_evidence_bundle
from .day_rollups import bundle_for_day, day_summary_from_summary, group_query_rows_by_day
from .patterns import build_recent_focus_loops, detect_anomalies, detect_episodes
from .trust import render_surface_freshness_markdown


@dataclass(frozen=True)
class PeriodReport:
    scale: str
    key: str
    markdown: str
    payload: dict[str, Any]
    output_path: Path | None
    wrote: bool


def build_period_report(
    scale: Any,
    key: str,
    *,
    output_root: Path | None = Path("artefacts/context/reports"),
    write_files: bool = True,
    persist_evidence: bool | None = None,
) -> PeriodReport:
    normalized = normalize_scale(scale)
    bundle = build_period_evidence_bundle(
        normalized,
        key,
        write=write_files if persist_evidence is None else persist_evidence,
    )
    summary = summarize_evidence_bundle(bundle)
    markdown = _render_period_report(bundle, summary)
    output_path = None
    wrote = False
    if write_files and output_root is not None:
        relpath = hierarchical_relpath(normalized, key)
        target = (output_root / relpath) if relpath is not None else (output_root / f"{key}.md")
        wrote = write_text_if_changed(target, markdown)
        output_path = target
    payload = {
        "period": bundle.period.to_dict(),
        "bundle_ref": bundle.bundle_ref,
        "summary": summary,
    }
    return PeriodReport(
        scale=normalized,
        key=key,
        markdown=markdown,
        payload=payload,
        output_path=output_path,
        wrote=wrote,
    )


def build_period_reports(
    start: date,
    end: date,
    *,
    scale: Any = "day",
    output_root: Path | None = Path("artefacts/context/reports"),
    write_files: bool = True,
    persist_evidence: bool | None = None,
) -> list[PeriodReport]:
    normalized = normalize_scale(scale)
    return [
        build_period_report(
            normalized,
            key,
            output_root=output_root,
            write_files=write_files,
            persist_evidence=persist_evidence,
        )
        for key in period_keys_in_range(normalized, start, end)
    ]


def summarize_evidence_bundle(bundle: EvidenceBundle) -> dict[str, Any]:
    summary = summarize_evidence_surfaces(bundle)
    summary["patterns"] = _summarize_patterns(bundle)
    return summary


def summarize_evidence_surfaces(bundle: EvidenceBundle) -> dict[str, Any]:
    queries = {query.query_id: query for query in bundle.queries}
    delivery_rows = queries.get("delivery_telemetry").rows if queries.get("delivery_telemetry") else []
    attention_rows = queries.get("project_attention").rows if queries.get("project_attention") else []
    chat_rows = queries.get("chat_activity").rows if queries.get("chat_activity") else []
    git_rows = queries.get("git_daily").rows if queries.get("git_daily") else []
    file_rows = queries.get("git_file_facts").rows if queries.get("git_file_facts") else []
    focus_span_rows = queries.get("focus_spans").rows if queries.get("focus_spans") else []
    focus_loop_rows = queries.get("focus_loops").rows if queries.get("focus_loops") else []
    context_switch_rows = queries.get("context_switches").rows if queries.get("context_switches") else []
    circadian_rows = queries.get("circadian").rows if queries.get("circadian") else []
    profile_rows = queries.get("polylogue_sessions").rows if queries.get("polylogue_sessions") else []
    query_rows = {query.query_id: query.row_count for query in bundle.queries}
    surfaces_present = [query.query_id for query in bundle.queries if query.row_count and not query.error]
    surfaces_with_errors = [query.query_id for query in bundle.queries if query.error]
    days_with_evidence = _count_distinct_dates(bundle.queries)
    period_days = (bundle.period.end - bundle.period.start).days + 1

    repo_counter: Counter[str] = Counter()
    model_counter: Counter[str] = Counter()
    active_hours = 0.0
    total_commits = 0
    command_count = 0
    chat_sessions = 0
    chat_engaged_minutes = 0.0
    for row in delivery_rows:
        active_hours += float(row.get("active_hours") or 0.0)
        total_commits += int(row.get("total_commits") or 0)
        command_count += int(row.get("command_count") or 0)
        chat_sessions += int(row.get("chat_sessions") or 0)
        chat_engaged_minutes += float(row.get("chat_engaged_minutes") or 0.0)
        repo_counter.update(_string_list(row.get("repos_json")))
        model_counter.update(_string_list(row.get("ai_models_json")))

    attention_project_counter: Counter[str] = Counter()
    attention_entropy_total = 0.0
    attention_rotation_total = 0.0
    attention_row_count = 0
    for row in attention_rows:
        top_project = row.get("top_project")
        if top_project:
            attention_project_counter[str(top_project)] += 1
        attention_entropy_total += float(row.get("entropy") or 0.0)
        attention_rotation_total += float(row.get("rotation_speed") or 0.0)
        attention_row_count += 1

    provider_counter: Counter[str] = Counter()
    work_kind_counter: Counter[str] = Counter()
    chat_project_counter: Counter[str] = Counter()
    total_messages = 0
    total_words = 0
    total_engaged_minutes = 0.0
    total_cost_usd = 0.0
    for row in chat_rows:
        provider_counter[str(row.get("provider") or "unknown")] += int(row.get("session_count") or 0)
        work_kind_counter[str(row.get("dominant_work_kind") or "unknown")] += int(row.get("session_count") or 0)
        chat_project_counter.update(_string_list(row.get("projects_json")))
        total_messages += int(row.get("total_messages") or 0)
        total_words += int(row.get("total_words") or 0)
        total_engaged_minutes += float(row.get("engaged_minutes") or 0.0)

    git_repo_counter: Counter[str] = Counter()
    git_churn_counter: Counter[str] = Counter()
    git_net_counter: Counter[str] = Counter()
    for row in git_rows:
        repo = _repo_label(row.get("repo"))
        git_repo_counter[repo] += int(row.get("commit_count") or 0)
        git_churn_counter[repo] += int(row.get("churn") or 0)
        git_net_counter[repo] += int(row.get("net_loc") or 0)

    file_counter: Counter[str] = Counter()
    for row in file_rows:
        path_root = str(row.get("path_root") or row.get("path") or "unknown")
        file_counter[path_root] += int(row.get("lines_changed") or 0)

    span_counter: Counter[str] = Counter()
    focus_mode_counter: Counter[str] = Counter()
    focus_project_counter: Counter[str] = Counter()
    for row in focus_span_rows:
        duration_minutes = int(float(row.get("duration_seconds") or 0.0) // 60)
        label = str(row.get("project") or row.get("app") or row.get("mode") or row.get("span_kind") or "unknown")
        span_counter[label] += duration_minutes
        mode = row.get("mode")
        if mode:
            focus_mode_counter[str(mode)] += duration_minutes
        project = row.get("project")
        if project:
            focus_project_counter[str(project)] += duration_minutes

    loop_counter: Counter[str] = Counter()
    for row in focus_loop_rows:
        label = str(row.get("dominant_project") or row.get("dominant_mode") or row.get("context_a_app") or "unknown")
        loop_counter[label] += int(round(float(row.get("duration_minutes") or 0.0)))

    switch_total = 0
    project_switch_total = 0
    mode_switch_total = 0
    avg_focus_total = 0.0
    longest_focus = 0.0
    fragmentation_total = 0.0
    switch_row_count = 0
    for row in context_switch_rows:
        switch_total += int(row.get("total_switches") or 0)
        project_switch_total += int(row.get("project_switches") or 0)
        mode_switch_total += int(row.get("mode_switches") or 0)
        avg_focus_total += float(row.get("avg_focus_minutes") or 0.0)
        longest_focus = max(longest_focus, float(row.get("longest_focus_minutes") or 0.0))
        fragmentation_total += float(row.get("fragmentation_score") or 0.0)
        switch_row_count += 1

    circadian_hour_counter: Counter[str] = Counter()
    circadian_mode_counter: Counter[str] = Counter()
    circadian_project_counter: Counter[str] = Counter()
    recovery_minutes_total = 0.0
    for row in circadian_rows:
        active_minutes = float(row.get("active_minutes") or 0.0)
        recovery_minutes = float(row.get("recovery_minutes") or 0.0)
        recovery_minutes_total += recovery_minutes
        if active_minutes <= 0:
            continue
        hour = int(row.get("hour") or 0)
        circadian_hour_counter[f"{hour:02d}:00"] += int(round(active_minutes))
        mode = row.get("dominant_mode")
        if mode:
            circadian_mode_counter[str(mode)] += int(round(active_minutes))
        project = row.get("dominant_project")
        if project:
            circadian_project_counter[str(project)] += int(round(active_minutes))

    session_title_counter: Counter[str] = Counter()
    session_project_counter: Counter[str] = Counter()
    for row in profile_rows:
        title = row.get("title")
        if title:
            session_title_counter[str(title)] += 1
        session_project_counter.update(_string_list(row.get("canonical_projects_json")))
        total_cost_usd += float(row.get("cost_usd") or 0.0)

    return {
        "evidence": {
            "query_rows": query_rows,
            "surfaces_present": surfaces_present,
            "surfaces_with_errors": surfaces_with_errors,
            "days_with_evidence": days_with_evidence,
            "period_days": period_days,
        },
        "delivery": {
            "active_hours": round(active_hours, 2),
            "total_commits": total_commits,
            "command_count": command_count,
            "chat_sessions": chat_sessions,
            "chat_engaged_minutes": round(chat_engaged_minutes, 1),
            "top_repos": repo_counter.most_common(5),
            "top_models": model_counter.most_common(5),
        },
        "attention": {
            "avg_entropy": round(attention_entropy_total / attention_row_count, 3) if attention_row_count else None,
            "avg_rotation_speed": round(attention_rotation_total / attention_row_count, 3) if attention_row_count else None,
            "top_projects": attention_project_counter.most_common(5),
        },
        "chat": {
            "providers": provider_counter.most_common(),
            "work_kinds": work_kind_counter.most_common(),
            "projects": chat_project_counter.most_common(5),
            "total_messages": total_messages,
            "total_words": total_words,
            "engaged_minutes": round(total_engaged_minutes, 1),
            "total_cost_usd": round(total_cost_usd, 4),
            "top_session_titles": session_title_counter.most_common(5),
            "top_session_projects": session_project_counter.most_common(5),
        },
        "git": {
            "repos": git_repo_counter.most_common(8),
            "churn": git_churn_counter.most_common(8),
            "net_loc": git_net_counter.most_common(8),
            "top_paths": file_counter.most_common(8),
        },
        "focus": {
            "top_spans": span_counter.most_common(8),
            "top_loops": loop_counter.most_common(8),
            "top_modes": focus_mode_counter.most_common(5),
            "top_projects": focus_project_counter.most_common(5),
            "total_switches": switch_total,
            "project_switches": project_switch_total,
            "mode_switches": mode_switch_total,
            "avg_focus_minutes": round(avg_focus_total / switch_row_count, 1) if switch_row_count else None,
            "longest_focus_minutes": round(longest_focus, 1) if longest_focus else None,
            "avg_fragmentation": round(fragmentation_total / switch_row_count, 3) if switch_row_count else None,
        },
        "circadian": {
            "active_minutes": circadian_hour_counter.most_common(6),
            "recovery_minutes_total": round(recovery_minutes_total, 1),
            "dominant_modes": circadian_mode_counter.most_common(5),
            "dominant_projects": circadian_project_counter.most_common(5),
        },
    }


def _summarize_patterns(bundle: EvidenceBundle) -> dict[str, Any]:
    grouped_rows = group_query_rows_by_day(bundle.queries)
    day_models = []
    current_day = bundle.period.start
    while current_day <= bundle.period.end:
        day_bundle = bundle_for_day(
            target=current_day,
            queries=bundle.queries,
            grouped_rows=grouped_rows,
            freshness=bundle.freshness,
        )
        day_models.append(day_summary_from_summary(current_day, summarize_evidence_surfaces(day_bundle)))
        current_day += timedelta(days=1)
    anomalies = detect_anomalies(day_models, include_processed=False)
    episodes = detect_episodes(day_models, anomalies=anomalies)
    focus_loop_rows = next((query.rows for query in bundle.queries if query.query_id == "focus_loops"), [])
    return {
        "episode_count": len(episodes),
        "episode_labels": [episode.label for episode in episodes[:8]],
        "episodes": [_episode_payload(episode) for episode in episodes[:8]],
        "anomaly_count": len(anomalies),
        "anomaly_kinds": sorted({anomaly.kind for anomaly in anomalies}),
        "anomalies": [anomaly.to_dict() for anomaly in anomalies[:12]],
        "recent_focus_loops": build_recent_focus_loops(focus_loop_rows, limit=8),
    }


def _render_period_report(bundle: EvidenceBundle, summary: dict[str, Any]) -> str:
    lines = [
        f"# {period_label(bundle.period.scale, bundle.period.key)}",
        "",
        f"- Scale: `{bundle.period.scale}`",
        f"- Key: `{bundle.period.key}`",
        f"- Range: `{bundle.period.start.isoformat()}` → `{bundle.period.end.isoformat()}`",
        f"- Evidence bundle: `{bundle.bundle_ref or 'n/a'}`",
        "",
        "## Evidence",
        "",
        f"- Days with evidence: {summary['evidence']['days_with_evidence']} / {summary['evidence']['period_days']}",
        f"- Surfaces present: {', '.join(summary['evidence']['surfaces_present']) or 'n/a'}",
        f"- Surface rows: {_format_mapping(summary['evidence']['query_rows'])}",
        "",
        "## Freshness",
        "",
        render_surface_freshness_markdown(bundle.freshness) or "- n/a",
        "",
        "## Delivery",
        "",
        f"- Active hours: {summary['delivery']['active_hours']}",
        f"- Commits: {summary['delivery']['total_commits']}",
        f"- Commands: {summary['delivery']['command_count']}",
        f"- Chat sessions: {summary['delivery']['chat_sessions']} ({summary['delivery']['chat_engaged_minutes']} engaged min)",
        f"- Repos: {_format_pairs(summary['delivery']['top_repos'])}",
        f"- AI models: {_format_pairs(summary['delivery']['top_models'])}",
        "",
        "## Attention",
        "",
        f"- Avg entropy: {_value(summary['attention']['avg_entropy'])}",
        f"- Avg rotation speed: {_value(summary['attention']['avg_rotation_speed'])}",
        f"- Top attention projects: {_format_pairs(summary['attention']['top_projects'])}",
        "",
        "## Conversation",
        "",
        f"- Providers: {_format_pairs(summary['chat']['providers'])}",
        f"- Work kinds: {_format_pairs(summary['chat']['work_kinds'])}",
        f"- Total messages / words: {summary['chat']['total_messages']} / {summary['chat']['total_words']}",
        f"- Engaged minutes / cost: {summary['chat']['engaged_minutes']} / {_value(summary['chat']['total_cost_usd'])}",
        f"- Session projects: {_format_pairs(summary['chat']['top_session_projects'])}",
        f"- Session titles: {_format_pairs(summary['chat']['top_session_titles'])}",
        "",
        "## Git",
        "",
        f"- Repos by commits: {_format_pairs(summary['git']['repos'])}",
        f"- Repos by churn: {_format_pairs(summary['git']['churn'])}",
        f"- Repos by net LOC: {_format_pairs(summary['git']['net_loc'])}",
        f"- Hot paths: {_format_pairs(summary['git']['top_paths'])}",
        "",
        "## Focus",
        "",
        f"- Top spans: {_format_pairs(summary['focus']['top_spans'], suffix='m')}",
        f"- Top loops: {_format_pairs(summary['focus']['top_loops'], suffix='m')}",
        f"- Top modes: {_format_pairs(summary['focus']['top_modes'], suffix='m')}",
        f"- Top projects: {_format_pairs(summary['focus']['top_projects'], suffix='m')}",
        f"- Total switches: {summary['focus']['total_switches']} (project {summary['focus']['project_switches']}, mode {summary['focus']['mode_switches']})",
        f"- Avg focus / longest focus: {_value(summary['focus']['avg_focus_minutes'])} / {_value(summary['focus']['longest_focus_minutes'])}",
        f"- Avg fragmentation: {_value(summary['focus']['avg_fragmentation'])}",
        "",
        "## Patterns",
        "",
        f"- Episodes: {summary['patterns']['episode_count']} ({_format_labels(summary['patterns']['episode_labels'])})",
        f"- Anomalies: {summary['patterns']['anomaly_count']} ({_format_labels(summary['patterns']['anomaly_kinds'])})",
        f"- Recent focus loops: {_format_focus_loops(summary['patterns']['recent_focus_loops'])}",
        "",
        "## Circadian",
        "",
        f"- Active minutes by hour: {_format_pairs(summary['circadian']['active_minutes'], suffix='m')}",
        f"- Recovery minutes total: {_value(summary['circadian']['recovery_minutes_total'])}",
        f"- Dominant modes: {_format_pairs(summary['circadian']['dominant_modes'], suffix='m')}",
        f"- Dominant projects: {_format_pairs(summary['circadian']['dominant_projects'], suffix='m')}",
        "",
    ]
    return "\n".join(lines)


def _format_pairs(values: list[tuple[str, Any]], *, suffix: str = "") -> str:
    if not values:
        return "n/a"
    rendered = []
    for label, value in values:
        rendered.append(f"{label} ({value}{suffix})")
    return ", ".join(rendered)


def _format_labels(values: list[str]) -> str:
    return ", ".join(values) if values else "n/a"


def _format_focus_loops(values: list[dict[str, Any]]) -> str:
    if not values:
        return "n/a"
    rendered = []
    for item in values[:5]:
        label = item.get("dominant_project") or item.get("dominant_mode") or "unknown"
        duration = item.get("duration_minutes") or 0
        start = str(item.get("start") or "")[:10]
        rendered.append(f"{label} ({duration}m on {start})")
    return ", ".join(rendered)


def _format_mapping(values: dict[str, Any]) -> str:
    if not values:
        return "n/a"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _count_distinct_dates(queries: list[EvidenceQuery]) -> int:
    dates = {
        date_key
        for query in queries
        if not query.error
        for row in query.rows
        for date_key in _row_dates(row)
    }
    return len(dates)


def _row_dates(row: dict[str, Any]) -> set[str]:
    dates: set[str] = set()
    for column in ("date", "start", "authored_at", "created_at", "first_message_at", "last_message_at"):
        date_key = _date_key(row.get(column))
        if date_key is not None:
            dates.add(date_key)
    return dates


def _date_key(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) >= 10:
            return stripped[:10]
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    parsed = json.loads(text)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _repo_label(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value)
    if "/" in text:
        return Path(text).name or text
    return text


def _value(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _episode_payload(episode) -> dict[str, object]:
    return {
        "episode_id": episode.episode_id,
        "label": episode.label,
        "start_date": episode.start_date.isoformat(),
        "end_date": episode.end_date.isoformat(),
        "days": episode.days,
        "active_hours": round(episode.active_seconds / 3600.0, 2),
        "dominant_mode": episode.dominant_mode,
        "dominant_project": episode.dominant_project,
        "dominant_topic": episode.dominant_topic,
        "trigger": episode.trigger,
        "confidence": round(episode.confidence, 3),
    }


def _emit_reports(reports: list[PeriodReport], *, json_out: bool) -> None:
    if json_out:
        for report in reports:
            print(json.dumps(report.payload, ensure_ascii=False, default=str))
        return
    for report in reports:
        print(report.markdown.rstrip())
        print()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Render generic period reports from evidence bundles.")
    parser.add_argument("start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--scale", default="day", help="Period scale: day, week, month, quarter, half, year.")
    parser.add_argument("--output", type=Path, default=Path("artefacts/context/reports"), help="Output root for rendered reports.")
    parser.add_argument("--write-files", action=argparse.BooleanOptionalAction, default=True, help="Control whether Markdown files are written.")
    parser.add_argument("--json", action="store_true", help="Emit JSON payloads instead of Markdown.")
    args = parser.parse_args()

    start = parse_iso_dateish(args.start)
    end = parse_iso_dateish(args.end)
    reports = build_period_reports(
        start,
        end,
        scale=args.scale,
        output_root=args.output,
        write_files=args.write_files,
    )
    _emit_reports(reports, json_out=args.json)
    if args.write_files:
        wrote_count = sum(1 for report in reports if report.wrote)
        print(f"Wrote {wrote_count} report(s) to {args.output}")


if __name__ == "__main__":
    cli()
