"""Sinnix NixOS generation activation history source.

Reads lynchpinGenerationLog activation script captures that record each
`nixos-rebuild switch` event. Use to correlate machine telemetry samples
with NixOS generation activations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.parse import as_local

__all__ = [
    "SinnixGeneration",
    "SinnixGenerationRecord",
    "SinnixGenerationsReadiness",
    "generation_records",
    "iter_generations",
    "daily_generations",
    "readiness",
]


@dataclass(frozen=True)
class SinnixGeneration:
    """A NixOS generation activation record (legacy strictly-typed shape).

    ``generation`` is int-parsed. Use ``SinnixGenerationRecord`` for the
    string-preserving variant the substrate promoter expects (codex's
    ``unknown`` generation tag can't survive int-conversion).
    """

    generation: int
    activated_at: datetime
    store_path: str
    sinnix_revision: str
    nixos_label: str
    host: str

    @property
    def date(self) -> date:
        """Return the date of activation (local timezone)."""
        return self.activated_at.date()


@dataclass(frozen=True)
class SinnixGenerationRecord:
    """NixOS generation activation, generation kept as string.

    Mirrors what's in generations.jsonl 1:1 — the ``generation`` column is
    declared VARCHAR in the substrate (a generation might be the literal
    ``"unknown"`` sentinel during recovery boots when the symlink target
    isn't parseable). Promoter expects this shape.
    """

    generation: str
    activated_at: datetime
    store_path: str
    sinnix_revision: str
    nixos_label: str
    host: str

    @property
    def date(self) -> date:
        return self.activated_at.date()


@dataclass(frozen=True)
class SinnixGenerationsReadiness:
    """Status of the generations.jsonl source product.

    ``status`` ∈ {"missing", "empty", "ok"}. ``row_count`` counts JSONL
    lines (not necessarily parseable rows — caller can subtract via
    ``generation_records`` for the precise figure).
    """

    status: str
    row_count: int
    path: Path


@dataclass(frozen=True)
class DailyGenerations:
    """Daily summary of NixOS generation activations."""

    date: date
    count: int
    hosts: frozenset[str]
    generations: tuple[int, ...]


def _canonical_generations_path() -> Path:
    """Return the canonical path to generations.jsonl."""
    return get_config().captures_root / "machine" / "generations.jsonl"


def readiness(*, path: Path | None = None) -> SinnixGenerationsReadiness:
    """Probe the generations.jsonl product's readiness.

    Returns ``missing`` if the file isn't present, ``empty`` if it exists
    but contains no JSONL lines, ``ok`` with the row count otherwise.
    """
    target = path or _canonical_generations_path()
    if not target.exists():
        return SinnixGenerationsReadiness(status="missing", row_count=0, path=target)
    row_count = 0
    try:
        with target.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row_count += 1
    except OSError:
        return SinnixGenerationsReadiness(status="missing", row_count=0, path=target)
    status = "ok" if row_count > 0 else "empty"
    return SinnixGenerationsReadiness(status=status, row_count=row_count, path=target)


def generation_records(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[SinnixGenerationRecord]:
    """Iterate generation records preserving generation as a string.

    Unlike ``iter_generations`` (int-parsed, drops "unknown"), this
    iterator keeps the raw string so promoters can store recovery-boot
    activation rows. Same date filtering and malformed-line resilience.
    """
    target = path or _canonical_generations_path()
    if not target.exists():
        return
    try:
        with target.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    activated_at = datetime.fromisoformat(
                        obj["activated_at"].replace("Z", "+00:00")
                    )
                    activated_at = as_local(activated_at)
                    if activated_at is None:
                        continue
                    if start and activated_at.date() < start:
                        continue
                    if end and activated_at.date() > end:
                        continue
                    yield SinnixGenerationRecord(
                        generation=str(obj.get("generation") or "unknown"),
                        activated_at=activated_at,
                        store_path=obj["store_path"],
                        sinnix_revision=obj["sinnix_revision"],
                        nixos_label=obj["nixos_label"],
                        host=obj["host"],
                    )
                except (KeyError, ValueError, TypeError):
                    continue
    except OSError:
        return


def iter_generations(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[SinnixGeneration]:
    """Iterate NixOS generation activation records.

    Args:
        start: Optional start date (inclusive).
        end: Optional end date (inclusive).
        path: Optional override path to generations.jsonl. Defaults to canonical path.

    Yields:
        SinnixGeneration records, ordered by activated_at.
    """
    if path is None:
        path = _canonical_generations_path()

    if not path.exists():
        return

    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                try:
                    # Parse ISO 8601 datetime
                    activated_at_str = obj["activated_at"]
                    activated_at = datetime.fromisoformat(activated_at_str.replace("Z", "+00:00"))
                    activated_at = as_local(activated_at)
                    if activated_at is None:
                        continue

                    act_date = activated_at.date()
                    if start and act_date < start:
                        continue
                    if end and act_date > end:
                        continue

                    gen = SinnixGeneration(
                        generation=int(obj["generation"]),
                        activated_at=activated_at,
                        store_path=obj["store_path"],
                        sinnix_revision=obj["sinnix_revision"],
                        nixos_label=obj["nixos_label"],
                        host=obj["host"],
                    )
                    yield gen
                except (KeyError, ValueError, TypeError):
                    continue
    except (OSError, IOError):
        return


def daily_generations(
    start: date,
    end: date,
    *,
    path: Path | None = None,
) -> Iterator[DailyGenerations]:
    """Aggregate generation activations by day.

    Args:
        start: Start date (inclusive).
        end: End date (inclusive).
        path: Optional override path to generations.jsonl.

    Yields:
        DailyGenerations records, one per day that had activations.
    """
    by_day: dict[date, tuple[set[str], list[int]]] = {}

    for gen in iter_generations(start=start, end=end, path=path):
        d = gen.date
        if d not in by_day:
            by_day[d] = (set(), [])
        hosts, gens = by_day[d]
        hosts.add(gen.host)
        gens.append(gen.generation)

    for d in sorted(by_day.keys()):
        hosts, gens = by_day[d]
        yield DailyGenerations(
            date=d,
            count=len(gens),
            hosts=frozenset(hosts),
            generations=tuple(sorted(gens)),
        )
