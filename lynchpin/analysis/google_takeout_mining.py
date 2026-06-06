"""Retrospective mining over canonical Google Takeout product events."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.sources.google_takeout_products import GoogleTakeoutEvent, iter_events


@dataclass(frozen=True)
class GoogleTakeoutServiceSummary:
    product: str
    service: str | None
    event_count: int
    active_days: int
    first_seen: date
    last_seen: date


@dataclass(frozen=True)
class GoogleTakeoutSearchTerm:
    term: str
    count: int
    first_seen: date
    last_seen: date
    services: tuple[str, ...]


@dataclass(frozen=True)
class GoogleTakeoutSession:
    start: datetime
    end: datetime
    duration_min: float
    event_count: int
    products: tuple[str, ...]
    services: tuple[str, ...]
    sample_titles: tuple[str, ...]


@dataclass(frozen=True)
class GoogleTakeoutAnomalyDay:
    date: date
    event_count: int
    robust_z: float
    top_products: tuple[tuple[str, int], ...]
    top_services: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class GoogleTakeoutMonthlyPattern:
    month: str
    event_count: int
    active_days: int
    top_products: tuple[tuple[str, int], ...]
    top_services: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class GoogleTakeoutCooccurrence:
    left: str
    right: str
    active_days: int
    jaccard: float


@dataclass(frozen=True)
class GoogleTakeoutRetrospective:
    start: date | None
    end: date | None
    event_count: int
    active_days: int
    product_summaries: tuple[GoogleTakeoutServiceSummary, ...]
    search_terms: tuple[GoogleTakeoutSearchTerm, ...]
    sessions: tuple[GoogleTakeoutSession, ...]
    anomaly_days: tuple[GoogleTakeoutAnomalyDay, ...]
    monthly_patterns: tuple[GoogleTakeoutMonthlyPattern, ...]
    cooccurrences: tuple[GoogleTakeoutCooccurrence, ...]

    def to_json(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def google_takeout_retrospective(
    *,
    start: date | None = None,
    end: date | None = None,
    session_gap_min: int = 45,
    top_n: int = 25,
    source_events: Iterable[GoogleTakeoutEvent] | None = None,
) -> GoogleTakeoutRetrospective:
    """Mine event-level Google Takeout into reusable retrospective structures.

    ``end`` is exclusive. The analysis is intentionally deterministic and
    parser-only: it does not infer semantics beyond explicit product/service
    labels and weak search-query extraction from My Activity titles.
    """
    events = [
        event
        for event in (
            source_events
            if source_events is not None
            else iter_events(start=start, end=end)
        )
        if _in_window(event, start=start, end=end)
    ]
    events.sort(key=lambda event: event.timestamp)

    product_summaries = _summarize_products(events, top_n=top_n)
    search_terms = _summarize_search_terms(events, top_n=top_n)
    sessions = _summarize_sessions(
        events,
        gap=timedelta(minutes=max(session_gap_min, 1)),
        top_n=top_n,
    )
    anomaly_days = _summarize_anomaly_days(events, top_n=top_n)
    monthly_patterns = _summarize_months(events, top_n=top_n)
    cooccurrences = _summarize_cooccurrences(events, top_n=top_n)

    active_days = {event.timestamp.date() for event in events}
    return GoogleTakeoutRetrospective(
        start=start,
        end=end,
        event_count=len(events),
        active_days=len(active_days),
        product_summaries=tuple(product_summaries),
        search_terms=tuple(search_terms),
        sessions=tuple(sessions),
        anomaly_days=tuple(anomaly_days),
        monthly_patterns=tuple(monthly_patterns),
        cooccurrences=tuple(cooccurrences),
    )


def write_google_takeout_retrospective(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    session_gap_min: int = 45,
    top_n: int = 50,
    source_events: Iterable[GoogleTakeoutEvent] | None = None,
) -> GoogleTakeoutRetrospective:
    report = google_takeout_retrospective(
        start=start,
        end=end,
        session_gap_min=session_gap_min,
        top_n=top_n,
        source_events=source_events,
    )
    payload = {
        "generated_at_utc": datetime.now().astimezone().isoformat(),
        **report.to_json(),
        "caveats": [
            "retrospective is parser-only over canonical Google Takeout product events",
            "sessions are temporal clusters; search terms are weak title parses",
        ],
    }
    save_json(out, payload, sort_keys=True)
    return report


def _in_window(
    event: GoogleTakeoutEvent,
    *,
    start: date | None,
    end: date | None,
) -> bool:
    day = event.timestamp.date()
    return (start is None or day >= start) and (end is None or day < end)


def _summarize_products(
    events: list[GoogleTakeoutEvent],
    *,
    top_n: int,
) -> list[GoogleTakeoutServiceSummary]:
    by_key: dict[tuple[str, str | None], list[GoogleTakeoutEvent]] = defaultdict(list)
    for event in events:
        by_key[(event.product, event.service)].append(event)
    rows = []
    for (product, service), group in by_key.items():
        days = [event.timestamp.date() for event in group]
        rows.append(
            GoogleTakeoutServiceSummary(
                product=product,
                service=service,
                event_count=len(group),
                active_days=len(set(days)),
                first_seen=min(days),
                last_seen=max(days),
            )
        )
    rows.sort(key=lambda row: (-row.event_count, row.product, row.service or ""))
    return rows[:top_n]


def _summarize_search_terms(
    events: list[GoogleTakeoutEvent],
    *,
    top_n: int,
) -> list[GoogleTakeoutSearchTerm]:
    counts: Counter[str] = Counter()
    days: dict[str, list[date]] = defaultdict(list)
    services: dict[str, set[str]] = defaultdict(set)
    for event in events:
        query = _search_query(event.title)
        if not query:
            continue
        counts[query] += 1
        days[query].append(event.timestamp.date())
        if event.service:
            services[query].add(event.service)
    rows = [
        GoogleTakeoutSearchTerm(
            term=term,
            count=count,
            first_seen=min(days[term]),
            last_seen=max(days[term]),
            services=tuple(sorted(services[term])),
        )
        for term, count in counts.most_common(top_n)
    ]
    return rows


def _search_query(title: str) -> str | None:
    text = re.sub(r"\s+", " ", title.replace("\xa0", " ")).strip()
    match = re.match(r"(?i)^searched for\s+(.+)$", text)
    if not match:
        return None
    query = match.group(1).strip().strip('"')
    if not query or len(query) < 2:
        return None
    return query.lower()


def _summarize_sessions(
    events: list[GoogleTakeoutEvent],
    *,
    gap: timedelta,
    top_n: int,
) -> list[GoogleTakeoutSession]:
    sessions: list[list[GoogleTakeoutEvent]] = []
    current: list[GoogleTakeoutEvent] = []
    previous: GoogleTakeoutEvent | None = None
    for event in events:
        if previous is not None and event.timestamp - previous.timestamp > gap:
            sessions.append(current)
            current = []
        current.append(event)
        previous = event
    if current:
        sessions.append(current)

    rows = [_session_summary(group) for group in sessions if len(group) >= 2]
    rows.sort(key=lambda row: (-row.event_count, -row.duration_min, row.start))
    return rows[:top_n]


def _session_summary(group: list[GoogleTakeoutEvent]) -> GoogleTakeoutSession:
    start = group[0].timestamp
    end = group[-1].timestamp
    products = Counter(event.product for event in group)
    services = Counter(event.service for event in group if event.service)
    titles = []
    seen_titles: set[str] = set()
    for event in group:
        title = event.title.strip()
        if title and title not in seen_titles:
            titles.append(title)
            seen_titles.add(title)
        if len(titles) >= 5:
            break
    return GoogleTakeoutSession(
        start=start,
        end=end,
        duration_min=round((end - start).total_seconds() / 60.0, 3),
        event_count=len(group),
        products=tuple(product for product, _ in products.most_common(5)),
        services=tuple(service for service, _ in services.most_common(5)),
        sample_titles=tuple(titles),
    )


def _summarize_anomaly_days(
    events: list[GoogleTakeoutEvent],
    *,
    top_n: int,
) -> list[GoogleTakeoutAnomalyDay]:
    by_day: dict[date, list[GoogleTakeoutEvent]] = defaultdict(list)
    for event in events:
        by_day[event.timestamp.date()].append(event)
    counts = {day: len(group) for day, group in by_day.items()}
    if not counts:
        return []
    values = sorted(counts.values())
    median = _median(values)
    deviations = [abs(value - median) for value in values]
    mad = _median(deviations) or 1.0
    rows = []
    for day, count in counts.items():
        group = by_day[day]
        robust_z = 0.6745 * (count - median) / mad
        if robust_z < 3.5 and count < median * 3:
            continue
        rows.append(
            GoogleTakeoutAnomalyDay(
                date=day,
                event_count=count,
                robust_z=round(robust_z, 3),
                top_products=tuple(Counter(event.product for event in group).most_common(5)),
                top_services=tuple(
                    Counter(event.service for event in group if event.service).most_common(5)
                ),
            )
        )
    rows.sort(key=lambda row: (-row.robust_z, -row.event_count, row.date))
    return rows[:top_n]


def _summarize_months(
    events: list[GoogleTakeoutEvent],
    *,
    top_n: int,
) -> list[GoogleTakeoutMonthlyPattern]:
    by_month: dict[str, list[GoogleTakeoutEvent]] = defaultdict(list)
    for event in events:
        by_month[event.timestamp.strftime("%Y-%m")].append(event)
    rows = []
    for month, group in by_month.items():
        rows.append(
            GoogleTakeoutMonthlyPattern(
                month=month,
                event_count=len(group),
                active_days=len({event.timestamp.date() for event in group}),
                top_products=tuple(Counter(event.product for event in group).most_common(5)),
                top_services=tuple(
                    Counter(event.service for event in group if event.service).most_common(5)
                ),
            )
        )
    rows.sort(key=lambda row: (-row.event_count, row.month))
    return rows[:top_n]


def _summarize_cooccurrences(
    events: list[GoogleTakeoutEvent],
    *,
    top_n: int,
) -> list[GoogleTakeoutCooccurrence]:
    service_days: dict[str, set[date]] = defaultdict(set)
    for event in events:
        label = event.service or event.product
        service_days[label].add(event.timestamp.date())
    labels = sorted(service_days)
    rows = []
    for i, left in enumerate(labels):
        for right in labels[i + 1:]:
            intersection = service_days[left] & service_days[right]
            if not intersection:
                continue
            union = service_days[left] | service_days[right]
            rows.append(
                GoogleTakeoutCooccurrence(
                    left=left,
                    right=right,
                    active_days=len(intersection),
                    jaccard=round(len(intersection) / len(union), 6),
                )
            )
    rows.sort(key=lambda row: (-row.jaccard, -row.active_days, row.left, row.right))
    return rows[:top_n]


def _median(values: list[int | float]) -> float:
    if not values:
        return 0.0
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return (float(values[midpoint - 1]) + float(values[midpoint])) / 2.0


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


__all__ = [
    "GoogleTakeoutAnomalyDay",
    "GoogleTakeoutCooccurrence",
    "GoogleTakeoutMonthlyPattern",
    "GoogleTakeoutRetrospective",
    "GoogleTakeoutSearchTerm",
    "GoogleTakeoutServiceSummary",
    "GoogleTakeoutSession",
    "google_takeout_retrospective",
    "write_google_takeout_retrospective",
]
