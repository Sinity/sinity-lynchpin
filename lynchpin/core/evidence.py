"""Shared evidence contracts used across graph, substrate, and analysis layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

CostClass = Literal["materialized", "network"]
# Per-query view of how a source intersects the requested window.
# "available"     — full coverage of the requested range.
# "partial"       — coverage interval intersects but does not contain the range.
# "out_of_range"  — source has known coverage that does not intersect the range.
#                   (No obstruction — the data is simply elsewhere on the
#                   timeline. E.g. a 2022 arbtt archive vs. a 2026 query.)
# "missing"       — source has no parsed rows / no materialized product.
# "blocked"       — source errored / cannot be read (genuine obstruction:
#                   missing credentials, broken file, parse failure).
# There is intentionally no "stale" status: dataset coverage is a property of
# the dataset (first_date..last_date), not a global freshness verdict.
ReadinessStatus = Literal["available", "partial", "out_of_range", "missing", "blocked"]


@dataclass(frozen=True)
class EvidenceProvenance:
    """Where an analysis fact came from and how expensive it is to refresh."""

    source: str
    cost: CostClass
    path: str | None = None
    generated_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class EvidenceCaveat:
    """A bounded warning attached to an analysis fact."""

    source: str
    status: ReadinessStatus
    message: str


def dedupe_caveats(caveats: tuple[EvidenceCaveat, ...]) -> tuple[EvidenceCaveat, ...]:
    """Preserve first-seen caveats while removing exact duplicates."""
    result: list[EvidenceCaveat] = []
    seen: set[tuple[str, ReadinessStatus, str]] = set()
    for caveat in caveats:
        key = (caveat.source, caveat.status, caveat.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(caveat)
    return tuple(result)


_STATUS_DEGRADATION: dict[ReadinessStatus, float] = {
    "available": 1.00,
    "partial": 0.90,
    "out_of_range": 0.40,
    "missing": 0.40,
    "blocked": 0.40,
}


def propagate_caveats(
    *caveat_sources: tuple[EvidenceCaveat, ...],
) -> tuple[EvidenceCaveat, ...]:
    """Union caveats across multiple node/edge/claim payloads."""
    flat: list[EvidenceCaveat] = []
    for caveats in caveat_sources:
        flat.extend(caveats)
    return dedupe_caveats(tuple(flat))


def degrade_confidence(
    base_confidence: float,
    caveats: tuple[EvidenceCaveat, ...],
    *,
    floor: float = 0.10,
) -> float:
    """Combine multipliers across all caveats; clamp at ``floor``."""
    if base_confidence <= 0.0:
        return 0.0
    multiplier = 1.0
    for caveat in dedupe_caveats(caveats):
        multiplier *= _STATUS_DEGRADATION.get(caveat.status, 1.0)
    return max(floor, base_confidence * multiplier)


def caveat_summary(caveats: tuple[EvidenceCaveat, ...]) -> dict[str, int]:
    """Compact rollup: status → count, useful for headers and badges."""
    counts: dict[str, int] = {}
    for caveat in dedupe_caveats(caveats):
        counts[caveat.status] = counts.get(caveat.status, 0) + 1
    return counts


@dataclass(frozen=True)
class SourceReadiness:
    """Availability, observed date bounds, and caveats for one source."""

    source: str
    status: ReadinessStatus
    reason: str
    cost: CostClass
    path: str | None = None
    count: int | None = None
    first_date: date | None = None
    last_date: date | None = None
    caveats: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status in {"available", "partial"}


@dataclass(frozen=True)
class SourceReadinessReport:
    """Readiness report for a date/range analysis run."""

    start: date
    end: date
    generated_at: datetime
    sources: tuple[SourceReadiness, ...]

    @property
    def caveats(self) -> tuple[EvidenceCaveat, ...]:
        result: list[EvidenceCaveat] = []
        for source in self.sources:
            if source.status == "available" and not source.caveats:
                continue
            messages = source.caveats or (source.reason,)
            result.extend(EvidenceCaveat(source.source, source.status, message) for message in messages)
        return tuple(result)

    def by_source(self) -> dict[str, SourceReadiness]:
        return {source.source: source for source in self.sources}


__all__ = [
    "CostClass",
    "SourceReadiness",
    "EvidenceCaveat",
    "EvidenceProvenance",
    "ReadinessStatus",
    "SourceReadinessReport",
    "caveat_summary",
    "dedupe_caveats",
    "degrade_confidence",
    "propagate_caveats",
]
