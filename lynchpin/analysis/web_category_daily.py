"""Per-day web-mode signal from categorized browsing history.

Joins raw browser visits (:mod:`lynchpin.sources.web`) to domain
classifications (:mod:`lynchpin.sources.web_categories`) and aggregates into one
typed record per logical day: visits and estimated minutes per category, a
sensitive-content visit share, and a coarse distraction ratio.

Minutes are *estimated*, not measured: browser history records point-in-time
visits with no dwell duration. We approximate per-visit dwell as the gap to the
next visit on the same day, capped at :data:`MAX_DWELL_MINUTES` so an idle tab
left open overnight does not inflate a category. This is a rough signal and is
documented as such; the visit counts are exact.

Missing != zero
---------------
Only days that actually have visits in the requested range produce a
``WebCategoryDay``. A day with no browsing yields no row rather than a row of
zeros — absence of data is not a measured zero.

API
---
    daily_web_categories(start, end) -> list[WebCategoryDay]
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date_type
from urllib.parse import urlparse

from ..core.primitives import logical_date
from ..sources.web import _iter_all_visits
from ..sources.web_categories import DomainCategory, classify_domains
from ..sources.web_urls import _normalize_domain

__all__ = [
    "WebCategoryDay",
    "MAX_DWELL_MINUTES",
    "DISTRACTION_CATEGORIES",
    "daily_web_categories",
]

#: A single visit can contribute at most this many estimated minutes. Caps the
#: gap-to-next-visit dwell heuristic so an abandoned tab does not dominate.
MAX_DWELL_MINUTES: float = 30.0

#: Categories treated as "distraction" for the coarse distraction ratio. This is
#: a heuristic lens, not a moral judgment: social/media/adult browsing is the
#: numerator, total categorized visits the denominator.
DISTRACTION_CATEGORIES: frozenset[str] = frozenset({"social", "media", "adult"})


@dataclass(frozen=True)
class WebCategoryDay:
    """Categorized browsing for one logical day.

    visits_by_category / minutes_by_category map every category that occurred on
    the day to its exact visit count and estimated dwell minutes. The
    ``nsfw_*`` compatibility fields summarize visits carrying the dedicated
    sensitive-content flag without retaining their URLs. ``distraction_ratio``
    is distraction-category visits over total visits.
    """

    date: _date_type
    total_visits: int
    total_minutes: float
    visits_by_category: dict[str, int]
    minutes_by_category: dict[str, float]
    nsfw_visits: int
    nsfw_visit_share: float
    distraction_ratio: float


@dataclass
class _DayBucket:
    visits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    minutes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    total_visits: int = 0
    total_minutes: float = 0.0
    nsfw_visits: int = 0
    distraction_visits: int = 0


def daily_web_categories(
    *, start: _date_type, end: _date_type, ensure: bool = True
) -> list[WebCategoryDay]:
    """Aggregate categorized browsing per logical day over ``[start, end]``.

    Pulls visits via the web source, classifies the distinct domain set once
    (seed + cached LLM), then buckets per logical day. Minutes are estimated via
    the capped gap-to-next-visit heuristic. Days with no visits produce no row.
    """
    # Collect visits with their normalized domain, sorted for dwell estimation.
    rows: list[tuple[object, str]] = []  # (visit, domain)
    domains: set[str] = set()
    for v in _iter_all_visits(start=start, end=end, ensure=ensure):
        domain = _normalize_domain(urlparse(v.url or "").netloc)
        if not domain:
            continue
        rows.append((v, domain))
        domains.add(domain)

    if not rows:
        return []

    classes: dict[str, DomainCategory] = classify_domains(domains)
    rows.sort(key=lambda r: r[0].timestamp)  # type: ignore[attr-defined]

    buckets: dict[_date_type, _DayBucket] = defaultdict(_DayBucket)

    for idx, (visit, domain) in enumerate(rows):
        ts = visit.timestamp  # type: ignore[attr-defined]
        day = logical_date(ts)
        dc = classes.get(
            domain, DomainCategory(domain, "other", False, "general")
        )

        # Dwell = gap to next visit on the SAME logical day, capped.
        dwell_min = 0.0
        if idx + 1 < len(rows):
            nxt = rows[idx + 1][0]
            if logical_date(nxt.timestamp) == day:  # type: ignore[attr-defined]
                gap_s = (nxt.timestamp - ts).total_seconds()  # type: ignore[attr-defined]
                if gap_s > 0:
                    dwell_min = min(gap_s / 60.0, MAX_DWELL_MINUTES)

        b = buckets[day]
        b.visits[dc.category] += 1
        b.minutes[dc.category] += dwell_min
        b.total_visits += 1
        b.total_minutes += dwell_min
        if dc.nsfw:
            b.nsfw_visits += 1
        if dc.category in DISTRACTION_CATEGORIES:
            b.distraction_visits += 1

    result: list[WebCategoryDay] = []
    for day in sorted(buckets):
        b = buckets[day]
        total = b.total_visits
        result.append(
            WebCategoryDay(
                date=day,
                total_visits=total,
                total_minutes=round(b.total_minutes, 2),
                visits_by_category=dict(b.visits),
                minutes_by_category={
                    k: round(v, 2) for k, v in b.minutes.items()
                },
                nsfw_visits=b.nsfw_visits,
                nsfw_visit_share=round(b.nsfw_visits / total, 4) if total else 0.0,
                distraction_ratio=(
                    round(b.distraction_visits / total, 4) if total else 0.0
                ),
            )
        )
    return result
