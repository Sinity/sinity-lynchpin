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


# ── M.16: Multi-layer caveat propagation ─────────────────────────────────────


# Per-status confidence multiplier when a caveat from that status appears in a
# composite claim (causal chain, dataset correlation, supported work claim, …).
# `missing` and `blocked` apply hard floors — when source data is gone, no
# confidence value is meaningful — but we still preserve the caveat record.
_STATUS_DEGRADATION: dict[ReadinessStatus, float] = {
    "available": 1.00,
    "partial":   0.90,
    "stale":     0.85,
    "missing":   0.40,
    "blocked":   0.40,
}


def propagate_caveats(
    *caveat_sources: tuple[EvidenceCaveat, ...],
) -> tuple[EvidenceCaveat, ...]:
    """Union caveats across multiple node/edge/claim payloads.

    Order-preserving across sources, dedup-aware. Use when a composite
    artifact (causal chain, dataset correlation, closure-chain, etc.)
    spans multiple evidence layers and the reader needs to see ALL the
    relevant warnings, not just the first layer's.
    """
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
    """Combine multipliers across all caveats; clamp at ``floor``.

    A claim that crosses three "partial" layers gets `0.90 ** 3 = 0.729×`
    of its original confidence, plus the caveats themselves are visible
    to the reader. Multiple caveats from the *same* source/status are
    combined once via :func:`dedupe_caveats` so a noisy single source
    doesn't compound.
    """
    if base_confidence <= 0.0:
        return 0.0
    deduped = dedupe_caveats(caveats)
    multiplier = 1.0
    for caveat in deduped:
        multiplier *= _STATUS_DEGRADATION.get(caveat.status, 1.0)
    return max(floor, base_confidence * multiplier)


def caveat_summary(caveats: tuple[EvidenceCaveat, ...]) -> dict[str, int]:
    """Compact rollup: status → count, useful for headers and badges."""
    summary: dict[str, int] = {}
    for caveat in dedupe_caveats(caveats):
        summary[caveat.status] = summary.get(caveat.status, 0) + 1
    return summary


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
