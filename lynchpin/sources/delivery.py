"""Cross-source daily delivery telemetry: AW + git + shell + chat → daily work shape.

Richer than DayFeatures for delivery-focused analysis: includes AI commit ratio,
commit/command density per active hour, repo breakdown, and engaged chat minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..core.primitives import date_to_dt_range
from .activitywatch import active_seconds_by_date

__all__ = [
    "DeliveryTelemetry",
    "daily_delivery",
]


@dataclass(frozen=True)
class DeliveryTelemetry:
    date: date
    active_hours: float
    total_commits: int
    ai_commits: int
    human_commits: int
    ai_ratio: float
    commit_density: float  # per active hour
    command_count: int
    command_density: float  # per active hour
    chat_sessions: int
    chat_engaged_min: float
    repos: tuple[str, ...]
    ai_models: tuple[str, ...]


def daily_delivery(*, start: date, end: date) -> list[DeliveryTelemetry]:
    from .git import daily_activity as git_daily
    from .terminal import shell_sessions
    from .polylogue import daily_activity as chat_daily

    active_map = {d: s / 3600 for d, s in active_seconds_by_date(start, end).items()}

    git_by_day: dict[date, dict] = {}
    for g in git_daily(start=start, end=end):
        bucket = git_by_day.setdefault(g.date, {"commits": 0, "ai": 0, "repos": set(), "authors": set()})
        bucket["commits"] += g.commit_count
        bucket["ai"] += g.ai_coauthored
        bucket["repos"].add(g.repo)
        bucket["authors"].update(g.authors)

    shell_by_day: dict[date, int] = {}
    s_dt, e_dt = date_to_dt_range(start, end)
    for sess in shell_sessions(start=s_dt, end=e_dt):
        shell_by_day[sess.start.date()] = shell_by_day.get(sess.start.date(), 0) + sess.command_count

    chat_by_day: dict[date, dict] = {}
    for c in chat_daily(start=start, end=end):
        bucket = chat_by_day.setdefault(c.date, {"sessions": 0, "minutes": 0.0})
        bucket["sessions"] += c.session_count
        bucket["minutes"] += c.engaged_minutes

    all_dates = sorted(set(active_map) | set(git_by_day) | set(shell_by_day) | set(chat_by_day))
    result: list[DeliveryTelemetry] = []
    for day in all_dates:
        hours = active_map.get(day, 0)
        git = git_by_day.get(day, {"commits": 0, "ai": 0, "repos": set(), "authors": set()})
        cmds = shell_by_day.get(day, 0)
        chat = chat_by_day.get(day, {"sessions": 0, "minutes": 0.0})
        total_c = git["commits"]
        ai_c = git["ai"]
        result.append(DeliveryTelemetry(
            date=day, active_hours=round(hours, 2),
            total_commits=total_c, ai_commits=ai_c, human_commits=max(total_c - ai_c, 0),
            ai_ratio=round(ai_c / total_c, 3) if total_c else 0,
            commit_density=round(total_c / max(hours, 0.1), 2),
            command_count=cmds, command_density=round(cmds / max(hours, 0.1), 2),
            chat_sessions=chat["sessions"], chat_engaged_min=round(chat["minutes"], 1),
            repos=tuple(sorted(git["repos"])), ai_models=tuple(sorted(git["authors"])),
        ))
    return result
