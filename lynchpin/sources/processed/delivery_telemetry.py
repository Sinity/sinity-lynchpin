"""Delivery telemetry: daily continuous work-shape metrics without rigid workflow labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, cast

from ._activitywatch import active_seconds_by_date


@dataclass(frozen=True)
class DeliveryTelemetry:
    date: date
    active_hours: float
    total_commits: int
    ai_commits: int
    human_commits: int
    ai_ratio: float
    commit_density_per_active_hour: float
    command_count: int
    command_density_per_active_hour: float
    chat_sessions: int
    chat_engaged_minutes: float
    chat_minutes_per_active_hour: float
    repos: tuple[str, ...]
    ai_models_used: tuple[str, ...]


def iter_delivery_telemetry(
    *,
    start: date,
    end: date,
) -> Iterator[DeliveryTelemetry]:
    from .chat_activity import iter_chat_daily
    from .git_activity import iter_git_daily
    from .shell_sessions import iter_shell_sessions

    active_hours_map = {
        day: seconds / 3600.0
        for day, seconds in active_seconds_by_date(start=start, end=end).items()
    }

    git_by_day: dict[date, dict[str, object]] = {}
    for activity in iter_git_daily(start=start, end=end):
        bucket = git_by_day.setdefault(
            activity.date,
            {"commits": 0, "ai": 0, "repos": set(), "authors": set()},
        )
        bucket["commits"] = int(bucket["commits"]) + activity.commit_count
        bucket["ai"] = int(bucket["ai"]) + activity.ai_coauthored
        cast(set[str], bucket["repos"]).add(activity.repo)
        cast(set[str], bucket["authors"]).update(activity.authors)

    shell_by_day: dict[date, int] = {}
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    for session in iter_shell_sessions(start=start_dt, end=end_dt):
        shell_by_day[session.start.date()] = shell_by_day.get(session.start.date(), 0) + session.command_count

    chat_by_day: dict[date, dict[str, float | int]] = {}
    for activity in iter_chat_daily(start=start, end=end):
        bucket = chat_by_day.setdefault(activity.date, {"sessions": 0, "minutes": 0.0})
        bucket["sessions"] = int(bucket["sessions"]) + activity.session_count
        bucket["minutes"] = float(bucket["minutes"]) + activity.engaged_minutes

    all_dates = sorted(set(active_hours_map) | set(git_by_day) | set(shell_by_day) | set(chat_by_day))
    for day in all_dates:
        hours = active_hours_map.get(day, 0.0)
        git = git_by_day.get(day, {"commits": 0, "ai": 0, "repos": set(), "authors": set()})
        shell_commands = shell_by_day.get(day, 0)
        chat = chat_by_day.get(day, {"sessions": 0, "minutes": 0.0})
        total_commits = int(git["commits"])
        ai_commits = int(git["ai"])
        human_commits = max(total_commits - ai_commits, 0)
        ai_ratio = ai_commits / total_commits if total_commits else 0.0
        yield DeliveryTelemetry(
            date=day,
            active_hours=hours,
            total_commits=total_commits,
            ai_commits=ai_commits,
            human_commits=human_commits,
            ai_ratio=ai_ratio,
            commit_density_per_active_hour=total_commits / max(hours, 0.1),
            command_count=shell_commands,
            command_density_per_active_hour=shell_commands / max(hours, 0.1),
            chat_sessions=int(chat["sessions"]),
            chat_engaged_minutes=float(chat["minutes"]),
            chat_minutes_per_active_hour=float(chat["minutes"]) / max(hours, 0.1),
            repos=tuple(sorted(cast(set[str], git["repos"]))),
            ai_models_used=tuple(sorted(cast(set[str], git["authors"]))),
        )
