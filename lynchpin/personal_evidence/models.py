"""Typed contracts for the private Personal Evidence Substrate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TemporalPrecision(StrEnum):
    """The resolution supported by a recorded temporal assertion."""

    UNKNOWN = "unknown"
    DATE = "date"
    MINUTE = "minute"
    SECOND = "second"
    INSTANT = "instant"
    INTERVAL = "interval"


class EpistemicStatus(StrEnum):
    """How directly a record is known, rather than whether it is current."""

    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    INTERPRETED = "interpreted"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class RecordStatus(StrEnum):
    """Lifecycle status for an evidence record or derived statement."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    DISPUTED = "disputed"
    ARCHIVED = "archived"


class ExportTier(StrEnum):
    """Distance of a record from the acquired source representation."""

    DIRECT = "direct"
    EXPORT = "export"
    NORMALIZED = "normalized"
    DERIVED = "derived"


class CoverageStatus(StrEnum):
    """A bounded statement about source availability over an interval."""

    COVERED = "covered"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TemporalBounds:
    """Valid time and transaction time carried by bitemporal records."""

    valid_from: datetime | None
    valid_to: datetime | None
    transaction_from: datetime
    transaction_to: datetime | None = None
    precision: TemporalPrecision = TemporalPrecision.UNKNOWN

    def __post_init__(self) -> None:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        if self.transaction_to is not None and self.transaction_to < self.transaction_from:
            raise ValueError("transaction_to must not precede transaction_from")


@dataclass(frozen=True)
class IncrementalRun:
    """Run identity and hashes carried with private-output records."""

    run_id: str
    run_hash: str
    input_hash: str
    parent_run_id: str | None = None


__all__ = [
    "CoverageStatus",
    "EpistemicStatus",
    "ExportTier",
    "IncrementalRun",
    "RecordStatus",
    "TemporalBounds",
    "TemporalPrecision",
]
