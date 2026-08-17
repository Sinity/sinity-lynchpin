"""Phone ambient-audio loudness levels.

Reads ``machine/phone/ambient-levels.jsonl`` — one row per ambient-audio
chunk (``chunk``, ``bytes``, ``duration_seconds``, ``sample_rate``,
``mean_db``, ``max_db``, ``captured_nothing``), append-only.

Real-data quirk (confirmed against the live file, 2026-08-14: 458 rows /
313 distinct chunks): a chunk MAY appear more than once. A repaired transfer
re-writes the same ``chunk`` name as a new line rather than replacing the
old one in place, so the file is not one-row-per-chunk despite ``chunk``
looking like a natural key. The newest record (last line in file order) for
a given chunk is the true one; ``ambient_levels`` dedupes on that basis
before yielding.

``chunk`` encodes its own UTC timestamp (``ambient-20260812T234714Z.m4a``),
which is the only date signal on this source — there is no separate ``ts``
field on these rows.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import LynchpinConfig
from ..core.parse import as_local, safe_float, safe_int
from ..core.source import SourceReadiness, file_readiness, read_jsonl_with

__all__ = [
    "AmbientLevel",
    "AmbientDay",
    "readiness",
    "ambient_levels",
    "daily_ambient",
]

_CHUNK_TS_RE = re.compile(r"ambient-(\d{8}T\d{6})Z")


@dataclass(frozen=True)
class AmbientLevel:
    chunk: str
    bytes: Optional[int]
    duration_seconds: Optional[float]
    sample_rate: Optional[int]
    mean_db: Optional[float]
    max_db: Optional[float]
    captured_nothing: bool

    @property
    def started_at(self) -> Optional[datetime]:
        match = _CHUNK_TS_RE.search(self.chunk)
        if match is None:
            return None
        try:
            naive_utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        except ValueError:
            return None
        return as_local(naive_utc.replace(tzinfo=timezone.utc))

    @property
    def date(self) -> Optional[date]:
        started = self.started_at
        return started.date() if started is not None else None


@dataclass(frozen=True)
class AmbientDay:
    date: date
    chunk_count: int
    mean_db_mean: Optional[float]
    max_db_max: Optional[float]
    silent_chunk_count: int


def readiness(path: Path | None = None) -> SourceReadiness:
    return file_readiness(path or LynchpinConfig.from_env().phone_ambient_jsonl)


def _hydrate(payload: dict[str, Any]) -> Optional[AmbientLevel]:
    chunk = payload.get("chunk")
    if not chunk:
        return None
    return AmbientLevel(
        chunk=str(chunk),
        bytes=safe_int(payload.get("bytes")),
        duration_seconds=safe_float(payload.get("duration_seconds")),
        sample_rate=safe_int(payload.get("sample_rate")),
        mean_db=safe_float(payload.get("mean_db")),
        max_db=safe_float(payload.get("max_db")),
        captured_nothing=bool(payload.get("captured_nothing")),
    )


def ambient_levels(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    path: Optional[Path] = None,
) -> Iterator[AmbientLevel]:
    """Yield one deduped ``AmbientLevel`` per chunk, oldest chunk first.

    Dedup keeps the LAST record seen per ``chunk`` (later lines in the
    append-only file win — see module docstring).
    """
    jsonl = path or LynchpinConfig.from_env().phone_ambient_jsonl
    latest: dict[str, AmbientLevel] = {}
    for level in read_jsonl_with(jsonl, _hydrate, source_name="phone_ambient"):
        latest[level.chunk] = level
    for level in sorted(latest.values(), key=lambda lvl: lvl.chunk):
        day = level.date
        if start is not None and (day is None or day < start):
            continue
        if end is not None and (day is None or day > end):
            continue
        yield level


def daily_ambient(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    path: Optional[Path] = None,
) -> list[AmbientDay]:
    """Per-day chunk count and loudness summary. Missing days are absent."""
    buckets: dict[date, list[AmbientLevel]] = defaultdict(list)
    for level in ambient_levels(start=start, end=end, path=path):
        if level.date is None:
            continue
        buckets[level.date].append(level)

    out: list[AmbientDay] = []
    for day, levels in sorted(buckets.items()):
        means = [lvl.mean_db for lvl in levels if lvl.mean_db is not None]
        maxes = [lvl.max_db for lvl in levels if lvl.max_db is not None]
        out.append(AmbientDay(
            date=day,
            chunk_count=len(levels),
            mean_db_mean=round(sum(means) / len(means), 2) if means else None,
            max_db_max=max(maxes) if maxes else None,
            silent_chunk_count=sum(1 for lvl in levels if lvl.captured_nothing),
        ))
    return out
