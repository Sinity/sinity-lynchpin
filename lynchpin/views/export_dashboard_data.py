#!/usr/bin/env python3
"""Export data for artifacts dashboard."""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any

from ..core.config import get_config

CFG = get_config()
REPO_ROOT = CFG.repo_root


def load_activity_timeline() -> List[Dict[str, Any]]:
    """Load activity timeline from baseline artifacts."""
    timeline_path = REPO_ROOT / "artefacts/core/baseline/latest/activity_timeline.json"
    if not timeline_path.exists():
        return []
    with timeline_path.open() as f:
        return json.load(f)


def load_recent_calendar(days: int = 7) -> List[Dict[str, Any]]:
    """Load recent calendar day summaries."""
    calendar_dir = REPO_ROOT / "artefacts/calendar/views"
    if not calendar_dir.exists():
        return []

    recent_days = []
    for i in range(days):
        date = datetime.now().date() - timedelta(days=i)
        day_file = calendar_dir / f"day-{date.isoformat()}.md"
        if day_file.exists():
            content = day_file.read_text()
            recent_days.append({
                "date": date.isoformat(),
                "content": content
            })

    return sorted(recent_days, key=lambda x: x["date"], reverse=True)


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


def export_dashboard_data(output_path: Path) -> None:
    """Export all dashboard data to a single JSON file."""
    timeline = load_activity_timeline()
    recent_calendar = load_recent_calendar(days=7)
    stats = get_summary_stats(timeline)

    # Last 90 days for charts
    recent_timeline = timeline[-90:] if len(timeline) >= 90 else timeline

    data = {
        "generated": datetime.now().isoformat(),
        "stats": stats,
        "timeline": recent_timeline,
        "recent_calendar": recent_calendar
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"Exported dashboard data to {output_path}")
    print(f"  Timeline: {len(timeline)} days ({len(recent_timeline)} in export)")
    print(f"  Calendar: {len(recent_calendar)} days")
    print(f"  Stats: {stats}")


def main():
    output_path = REPO_ROOT / "artefacts/assets/dashboard-data.json"
    export_dashboard_data(output_path)


if __name__ == "__main__":
    main()
