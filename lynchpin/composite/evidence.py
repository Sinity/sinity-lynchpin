"""Shared evidence contracts for high-level Lynchpin analysis products."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

CostClass = Literal["local-fast", "local-heavy", "network"]
ReadinessStatus = Literal["available", "partial", "stale", "missing", "blocked"]


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


@dataclass(frozen=True)
class SourceReadiness:
    """Availability, freshness, and caveats for one source."""

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
        return self.status in {"available", "partial", "stale"}


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
    "dedupe_caveats",
    "EvidenceCaveat",
    "EvidenceProvenance",
    "ReadinessStatus",
    "SourceReadiness",
    "SourceReadinessReport",
]
