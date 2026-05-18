"""Sinnix NixOS generation activation log source.

Reads the JSONL file written by sinnix's `lynchpinGenerationLog`
activation script (one line per `nixos-rebuild switch` activation).
Each line records the generation number, activation timestamp,
configurationRevision (sinnix git sha or "dirty"/"unknown"), NixOS
label, store path, and host.

Purpose: provides the join surface for "what changed at generation N?"
queries against machine telemetry rows — given a substrate row's
observed_at, look up the most recent SinnixGenerationRecord with
activated_at <= observed_at and read sinnix_revision to anchor the
window in sinnix git history.

Path resolves from `LynchpinConfig.sinnix_generations_jsonl`
(default: /realm/data/captures/machine/generations.jsonl).
Missing or empty file is treated as "no rows" — hosts that disabled
machine-telemetry simply produce no records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from ..core.config import LynchpinConfig
from ..core.parse import parse_datetime as _parse_dt
from ..core.source import SourceReadiness, file_readiness, read_jsonl_with


@dataclass(frozen=True)
class SinnixGenerationRecord:
    """One nixos-rebuild switch activation event."""
    generation: str
    activated_at: datetime
    store_path: str
    sinnix_revision: str
    nixos_label: str
    host: str


def readiness(path: Path | None = None) -> SourceReadiness:
    return file_readiness(path or LynchpinConfig.from_env().sinnix_generations_jsonl)


def _hydrate(payload: dict) -> SinnixGenerationRecord | None:
    activated_at = _parse_dt(payload.get("activated_at"))
    if activated_at is None:
        return None
    return SinnixGenerationRecord(
        generation=str(payload.get("generation") or "unknown"),
        activated_at=activated_at,
        store_path=str(payload.get("store_path") or ""),
        sinnix_revision=str(payload.get("sinnix_revision") or "unknown"),
        nixos_label=str(payload.get("nixos_label") or ""),
        host=str(payload.get("host") or ""),
    )


def generation_records(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[SinnixGenerationRecord]:
    """Yield SinnixGenerationRecord rows from the JSONL log."""
    jsonl = path or LynchpinConfig.from_env().sinnix_generations_jsonl
    for record in read_jsonl_with(jsonl, _hydrate, source_name="sinnix_generations"):
        if start is not None and record.activated_at.date() < start:
            continue
        if end is not None and record.activated_at.date() > end:
            continue
        yield record
