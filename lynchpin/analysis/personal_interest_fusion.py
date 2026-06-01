"""Weak-label personal interest traces across searches, bookmarks, and web domains."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from lynchpin.sources.bookmarks import BookmarkEvent, iter_bookmarks
from lynchpin.sources.google_takeout_products import GoogleTakeoutEvent, iter_events
from lynchpin.sources.web import domain_breakdown


STOPWORDS = {
    "and", "are", "com", "for", "from", "http", "https", "into", "the",
    "this", "that", "with", "www", "searched", "search", "google",
    "new", "opened", "used", "viewed", "visited", "watched",
}


@dataclass(frozen=True)
class InterestTopicTrace:
    topic: str
    score: float
    sources: tuple[str, ...]
    active_days: int
    first_seen: date
    last_seen: date
    source_counts: dict[str, int]
    sample_evidence: tuple[str, ...]


@dataclass(frozen=True)
class PersonalInterestReport:
    start: date | None
    end: date | None
    topic_count: int
    topics: tuple[InterestTopicTrace, ...]
    caveats: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def personal_interest_trace(
    *,
    start: date | None = None,
    end: date | None = None,
    top_n: int = 50,
    google_events: Iterable[GoogleTakeoutEvent] | None = None,
    bookmark_events: Iterable[BookmarkEvent] | None = None,
    web_domain_rows: Iterable[tuple[str, int, float]] | None = None,
) -> PersonalInterestReport:
    evidence: dict[str, list[tuple[str, date, str]]] = defaultdict(list)
    _add_google_evidence(evidence, google_events, start=start, end=end)
    _add_bookmark_evidence(evidence, bookmark_events, start=start, end=end)
    _add_web_domain_evidence(evidence, web_domain_rows, start=start, end=end)

    topics = [_topic_trace(topic, rows) for topic, rows in evidence.items()]
    topics.sort(key=lambda row: (-row.score, -row.active_days, row.topic))
    topics = topics[: max(top_n, 1)]
    return PersonalInterestReport(
        start=start,
        end=end,
        topic_count=len(topics),
        topics=tuple(topics),
        caveats=(
            "topics are weak lexical/domain labels, not semantic embeddings",
            "source reinforcement raises priority but does not prove importance or intent",
        ),
    )


def _add_google_evidence(
    evidence: dict[str, list[tuple[str, date, str]]],
    events: Iterable[GoogleTakeoutEvent] | None,
    *,
    start: date | None,
    end: date | None,
) -> None:
    rows = events if events is not None else iter_events()
    for event in rows:
        day = event.timestamp.date()
        if not _in_window(day, start=start, end=end):
            continue
        for topic in _topics(event.title):
            evidence[topic].append(("google_takeout", day, event.title[:120]))


def _add_bookmark_evidence(
    evidence: dict[str, list[tuple[str, date, str]]],
    events: Iterable[BookmarkEvent] | None,
    *,
    start: date | None,
    end: date | None,
) -> None:
    try:
        rows = events if events is not None else iter_bookmarks()
        for event in rows:
            if event.added_at is None:
                continue
            day = event.added_at.date()
            if not _in_window(day, start=start, end=end):
                continue
            for topic in {*_topics(event.title), _domain_topic(event.domain)}:
                if topic:
                    evidence[topic].append(("bookmarks", day, event.title or event.domain))
    except FileNotFoundError:
        return


def _add_web_domain_evidence(
    evidence: dict[str, list[tuple[str, date, str]]],
    rows: Iterable[tuple[str, int, float]] | None,
    *,
    start: date | None,
    end: date | None,
) -> None:
    try:
        if rows is None:
            if start is None or end is None:
                return
            domain_rows = domain_breakdown(start=start, end=end, top_n=200)
        else:
            domain_rows = rows
        anchor_day = start or end or date.today()
        for domain, count, _pct in domain_rows:
            topic = _domain_topic(domain)
            if not topic:
                continue
            for _ in range(min(int(count), 100)):
                evidence[topic].append(("webhistory", anchor_day, domain))
    except FileNotFoundError:
        return


def _topic_trace(topic: str, rows: list[tuple[str, date, str]]) -> InterestTopicTrace:
    source_counts = Counter(source for source, _day, _sample in rows)
    days = [day for _source, day, _sample in rows]
    samples = []
    seen: set[str] = set()
    for _source, _day, sample in rows:
        if sample and sample not in seen:
            samples.append(sample)
            seen.add(sample)
        if len(samples) >= 5:
            break
    reinforcement = len(source_counts)
    score = len(rows) * (1.0 + 0.35 * max(reinforcement - 1, 0))
    return InterestTopicTrace(
        topic=topic,
        score=round(score, 3),
        sources=tuple(sorted(source_counts)),
        active_days=len(set(days)),
        first_seen=min(days),
        last_seen=max(days),
        source_counts=dict(sorted(source_counts.items())),
        sample_evidence=tuple(samples),
    )


def _topics(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text.lower())
        if token not in STOPWORDS and not token.isdigit()
    }
    return tokens


def _domain_topic(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = [part for part in domain.lower().split(".") if part and part not in STOPWORDS]
    if not parts:
        return None
    return parts[-2] if len(parts) >= 2 else parts[0]


def _in_window(day: date, *, start: date | None, end: date | None) -> bool:
    return (start is None or day >= start) and (end is None or day < end)


def _json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


__all__ = ["InterestTopicTrace", "PersonalInterestReport", "personal_interest_trace"]
