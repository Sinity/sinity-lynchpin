#!/usr/bin/env python3
"""Export data for artifacts dashboard."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List

from ..sources.captures import activitywatch, atuin
from ..sources.indices import gitstats, sessions
from .calendar_summary import _afk_split, _focus_minutes, _git_summary


def _build_history(days: int) -> List[Dict[str, Any]]:
    today = datetime.now().date()
    start_date = today - timedelta(days=days - 1)
    session_map = defaultdict(list)
    for record in sessions.iter_sessions():
        if start_date <= record.date <= today:
            session_map[record.date].append(record)
    git_map = defaultdict(list)
    for commit in gitstats.iter_commits():
        if start_date <= commit.date <= today:
            git_map[commit.date].append(commit)
    records: List[Dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        target = today - timedelta(days=offset)
        day_metrics = _day_metrics(
            target,
            session_records=session_map.get(target, []),
            git_commits=git_map.get(target, []),
        )
        records.append(
            {
                "date": target.isoformat(),
                "active_hours": day_metrics["active_hours"],
                "afk_hours": day_metrics["afk_hours"],
                "window_hours": day_metrics["window_hours"],
                "command_total": day_metrics["command_total"],
                "codex_sessions": day_metrics["codex_sessions"],
                "git_commits": day_metrics["git_commits"],
                "focus_minutes": day_metrics["focus_minutes"],
                "top_apps": day_metrics["top_apps"],
            }
        )
    return records


def _build_recent_calendar(history: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    recent_days: List[Dict[str, Any]] = []
    for day_metrics in reversed(history[-days:]):
        recent_days.append(
            {
                "date": day_metrics["date"],
                "focus_minutes": day_metrics["focus_minutes"],
                "command_total": day_metrics["command_total"],
                "git_commits": day_metrics["git_commits"],
                "active_hours": day_metrics["active_hours"],
                "top_apps": day_metrics["top_apps"],
            }
        )
    return recent_days


def _day_metrics(target, *, session_records, git_commits) -> Dict[str, Any]:
    start = datetime.combine(target, datetime.min.time(), tzinfo=datetime.now().astimezone().tzinfo)
    end = start + timedelta(days=1)
    windows = list(activitywatch.window_events(day=target))
    afk = list(activitywatch.afk_events(day=target))
    commands = list(atuin.iter_commands(start=start, end=end))
    focus_counter = _focus_minutes(windows)
    active_hours, afk_hours = _afk_split(afk, windows)
    git_summary = _git_summary(list(git_commits))
    return {
        "date": target.isoformat(),
        "active_hours": round(active_hours, 2),
        "afk_hours": round(afk_hours, 2),
        "window_hours": round(sum(focus_counter.values()) / 60.0, 2),
        "command_total": len(commands),
        "codex_sessions": sum(1 for record in session_records if "codex" in record.provider.lower()),
        "git_commits": git_summary.commits,
        "focus_minutes": round(sum(focus_counter.values()), 1),
        "top_apps": [name for name, _minutes in focus_counter.most_common(3)],
    }


def get_summary_stats(timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics from timeline."""
    if not timeline:
        return {}

    recent_30 = timeline[-30:] if len(timeline) >= 30 else timeline
    recent_7 = timeline[-7:] if len(timeline) >= 7 else timeline

    total_active_hours = sum(d.get("active_hours", 0) for d in timeline)
    total_commands = sum(d.get("command_total", 0) for d in timeline)
    total_codex_sessions = sum(d.get("codex_sessions", 0) for d in timeline)

    avg_active_30d = sum(d.get("active_hours", 0) for d in recent_30) / max(len(recent_30), 1)
    avg_active_7d = sum(d.get("active_hours", 0) for d in recent_7) / max(len(recent_7), 1)

    return {
        "total_days": len(timeline),
        "total_active_hours": round(total_active_hours, 1),
        "total_commands": total_commands,
        "total_codex_sessions": total_codex_sessions,
        "avg_active_hours_30d": round(avg_active_30d, 1),
        "avg_active_hours_7d": round(avg_active_7d, 1),
        "date_range": {
            "start": timeline[0]["date"] if timeline else None,
            "end": timeline[-1]["date"] if timeline else None
        }
    }


def export_dashboard_data(
    output_path: Path,
    *,
    timeline_days: int = 90,
    recent_days: int = 7,
) -> None:
    """Export all dashboard data to a single JSON file."""
    timeline = _build_history(timeline_days)
    recent_calendar = _build_recent_calendar(timeline, days=recent_days)
    stats = get_summary_stats(timeline)

    data = {
        "generated": datetime.now().isoformat(),
        "stats": stats,
        "timeline": timeline,
        "recent_calendar": recent_calendar,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"Exported dashboard data to {output_path}")
    print(f"  Timeline: {len(timeline)} days")
    print(f"  Calendar: {len(recent_calendar)} days")
    print(f"  Stats: {stats}")


def main():
    output_path = Path(__file__).resolve().parents[2] / "artefacts/assets/dashboard-data.json"
    export_dashboard_data(output_path)


if __name__ == "__main__":
    main()
