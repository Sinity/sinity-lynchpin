"""Processed chat activity views — daily aggregates from Polylogue session profiles."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator

from ..exports.polylogue import iter_session_profiles

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatDayActivity:
    date: date
    provider: str
    session_count: int
    total_messages: int
    total_words: int
    total_wall_minutes: float
    dominant_work_kind: str | None
    projects: tuple[str, ...]


def iter_chat_daily(*, start: date, end: date) -> Iterator[ChatDayActivity]:
    """Yield per-provider daily chat activity summaries."""
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

    groups: dict[tuple[date, str], list] = defaultdict(list)

    for profile in iter_session_profiles(start=start_dt, end=end_dt):
        # Determine the date for this session
        session_dt = profile.first_message_at or profile.created_at
        if session_dt is None:
            continue
        session_date = session_dt.date() if isinstance(session_dt, datetime) else session_dt
        if not (start <= session_date <= end):
            continue
        groups[(session_date, profile.provider)].append(profile)

    for (d, provider), profiles in sorted(groups.items()):
        total_messages = sum(p.message_count for p in profiles)
        total_words = sum(p.word_count for p in profiles)
        total_wall_ms = sum(p.wall_duration_ms for p in profiles)
        total_wall_minutes = total_wall_ms / 60_000.0

        # Dominant work kind: most common across all work events in the group
        kind_counter: Counter[str] = Counter()
        all_projects: set[str] = set()
        for p in profiles:
            for event in p.work_events:
                kind_counter[event.kind.value if hasattr(event.kind, "value") else str(event.kind)] += 1
            all_projects.update(p.canonical_projects)

        dominant = kind_counter.most_common(1)[0][0] if kind_counter else None

        yield ChatDayActivity(
            date=d,
            provider=provider,
            session_count=len(profiles),
            total_messages=total_messages,
            total_words=total_words,
            total_wall_minutes=total_wall_minutes,
            dominant_work_kind=dominant,
            projects=tuple(sorted(all_projects)),
        )
