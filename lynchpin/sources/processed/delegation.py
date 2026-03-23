"""Delegation metrics: AI vs human commit patterns and collaboration modes."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator


@dataclass(frozen=True)
class DelegationMetrics:
    date: date
    total_commits: int
    ai_commits: int
    human_commits: int
    ai_ratio: float
    commits_per_tracked_hour: float
    delegation_mode: str
    ai_models_used: tuple[str, ...]
    chat_sessions: int
    chat_minutes: float


def iter_delegation_metrics(
    *, start: date, end: date
) -> Iterator[DelegationMetrics]:
    from .chat_activity import iter_chat_daily
    from .git_activity import iter_git_daily

    # Get active hours from warehouse
    active_hours_map: dict[date, float] = {}
    try:
        r = subprocess.run(
            [
                "duckdb",
                "artefacts/lynchpin/warehouse.duckdb",
                "-c",
                f"SELECT date, active_seconds/3600.0 FROM trajectory_day WHERE date BETWEEN '{start}' AND '{end}'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().split("\n"):
            parts = line.strip().split("│")
            if len(parts) >= 3:
                try:
                    d = date.fromisoformat(parts[1].strip())
                    h = float(parts[2].strip())
                    active_hours_map[d] = h
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass

    # Aggregate git by day
    git_by_day: dict[date, dict] = {}
    for g in iter_git_daily(start=start, end=end):
        if g.date not in git_by_day:
            git_by_day[g.date] = {"commits": 0, "ai": 0, "authors": set()}
        git_by_day[g.date]["commits"] += g.commit_count
        git_by_day[g.date]["ai"] += g.ai_coauthored
        git_by_day[g.date]["authors"].update(g.authors)

    # Aggregate chat by day
    chat_by_day: dict[date, dict] = {}
    for c in iter_chat_daily(start=start, end=end):
        if c.date not in chat_by_day:
            chat_by_day[c.date] = {"sessions": 0, "minutes": 0.0}
        chat_by_day[c.date]["sessions"] += c.session_count
        chat_by_day[c.date]["minutes"] += c.total_wall_minutes

    all_dates = sorted(
        set(
            list(git_by_day.keys())
            + list(chat_by_day.keys())
            + list(active_hours_map.keys())
        )
    )
    for d in all_dates:
        if d < start or d > end:
            continue
        git = git_by_day.get(d, {"commits": 0, "ai": 0, "authors": set()})
        chat = chat_by_day.get(d, {"sessions": 0, "minutes": 0.0})
        hours = active_hours_map.get(d, 0.0)
        total = git["commits"]
        ai = git["ai"]
        ratio = ai / max(total, 1)
        cph = total / max(hours, 0.1)

        # Classification based primarily on commits_per_tracked_hour.
        # Co-authorship tags are unreliable (nearly all commits are AI-assisted
        # but tags depend on tool workflow — Claude Code adds them, interactive
        # rebase loses them, manual commits lack them).
        if cph < 3:
            mode = "deep_work"
        elif cph > 25:
            mode = "fleet_orchestration"
        elif cph > 10:
            mode = "batch_delegation"
        else:
            mode = "pair_programming"

        yield DelegationMetrics(
            date=d,
            total_commits=total,
            ai_commits=ai,
            human_commits=total - ai,
            ai_ratio=ratio,
            commits_per_tracked_hour=cph,
            delegation_mode=mode,
            ai_models_used=tuple(sorted(git["authors"])),
            chat_sessions=chat["sessions"],
            chat_minutes=chat["minutes"],
        )
