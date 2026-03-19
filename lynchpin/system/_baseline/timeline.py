"""Timeline merge helpers for baseline rebuilds."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .shared import round_metric


def build_activity_timeline(
    window_daily: list[dict[str, Any]],
    afk_daily: list[dict[str, Any]],
    codex_daily: Iterable[Iterable[Any]],
    atuin_daily: Iterable[Iterable[Any]],
    command_categories: dict[str, Counter],
) -> list[dict[str, Any]]:
    dates: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"active_hours": 0.0, "afk_hours": 0.0, "window_hours": 0.0}
    )

    for entry in window_daily:
        dates[entry["date"]]["window_hours"] += entry.get("hours", 0.0)

    for entry in afk_daily:
        dates[entry["date"]]["active_hours"] += entry.get("active_hours", 0.0)
        dates[entry["date"]]["afk_hours"] += entry.get("afk_hours", 0.0)

    for date, count in codex_daily:
        dates[date]["codex_sessions"] = dates[date].get("codex_sessions", 0) + int(count)

    for date, count in atuin_daily:
        dates[date]["command_total"] = dates[date].get("command_total", 0) + int(count)

    for date, counter in command_categories.items():
        dates[date]["command_categories"] = {
            key: int(value) for key, value in sorted(counter.items())
        }

    timeline: list[dict[str, Any]] = []
    for date in sorted(dates.keys()):
        payload = {"date": date}
        payload.update(dates[date])
        payload.setdefault("codex_sessions", 0)
        payload.setdefault("command_total", 0)
        payload.setdefault("command_categories", {})
        payload["active_hours"] = round_metric(payload.get("active_hours", 0.0), 2)
        payload["afk_hours"] = round_metric(payload.get("afk_hours", 0.0), 2)
        payload["window_hours"] = round_metric(payload.get("window_hours", 0.0), 2)
        timeline.append(payload)
    return timeline
