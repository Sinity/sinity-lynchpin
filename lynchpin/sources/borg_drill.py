"""Borg drill (random-archive deep-verify) source.

Reads the JSONL log written by sinnix's `sinnix-borg-drill` systemd
service, which picks one random archive per Borg repo per week and
runs `borg check --verify-data` against it, recording timing + outcome.

The drill complements the cheap `borg check --repository-only` (chunk-
graph metadata only) by sampling chunk-content integrity. Without
periodic deep verification, silent bit rot in the chunk store would
not surface until restore time.

Path resolves from `LynchpinConfig.borg_drill_jsonl` with default
/realm/data/captures/machine/borg_drill.jsonl. Missing or empty file
is treated as "no rows" — hosts without the drill service produce
no records.
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
class BorgDrillRun:
    """One sinnix-borg-drill invocation result."""
    repo: str
    archive: str
    started_at: datetime
    ended_at: datetime
    duration_s: int
    exit_code: int
    status: str
    stderr_tail: str
    within_days: int


def readiness(path: Path | None = None) -> SourceReadiness:
    return file_readiness(path or LynchpinConfig.from_env().borg_drill_jsonl)


def _hydrate(payload: dict) -> BorgDrillRun | None:
    started_at = _parse_dt(payload.get("started_at"))
    ended_at = _parse_dt(payload.get("ended_at"))
    if started_at is None or ended_at is None:
        return None
    return BorgDrillRun(
        repo=str(payload.get("repo") or ""),
        archive=str(payload.get("archive") or ""),
        started_at=started_at,
        ended_at=ended_at,
        duration_s=int(payload.get("duration_s") or 0),
        exit_code=int(payload.get("exit_code") or 0),
        status=str(payload.get("status") or "unknown"),
        stderr_tail=str(payload.get("stderr_tail") or ""),
        within_days=int(payload.get("within_days") or 0),
    )


def drill_runs(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[BorgDrillRun]:
    """Yield BorgDrillRun rows from the JSONL log."""
    jsonl = path or LynchpinConfig.from_env().borg_drill_jsonl
    for run in read_jsonl_with(jsonl, _hydrate, source_name="borg_drill"):
        if start is not None and run.started_at.date() < start:
            continue
        if end is not None and run.started_at.date() > end:
            continue
        yield run
