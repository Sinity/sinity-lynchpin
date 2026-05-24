"""Canonical ARBTT focus-event reader."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

__all__ = [
    "ArbttFocusEvent",
    "ArbttDayActivity",
    "arbtt_events_path",
    "arbtt_manifest_path",
    "iter_arbtt_events",
    "daily_arbtt_activity",
]


@dataclass(frozen=True)
class ArbttFocusEvent:
    event_id: str
    timestamp: datetime
    duration_s: float
    program: str
    title: str
    category: str
    tags: tuple[str, ...]
    project: str | None
    source_path: str
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArbttDayActivity:
    date: date
    active_minutes: float
    event_count: int
    program_count: int


def arbtt_events_path(root: Path | None = None) -> Path:
    base = root or get_config().arbtt_root
    return base / "processed/events.ndjson"


def arbtt_manifest_path(root: Path | None = None) -> Path:
    return arbtt_events_path(root).with_suffix(".manifest.json")


def iter_arbtt_events(path: Path | None = None) -> Iterator[ArbttFocusEvent]:
    target = path or arbtt_events_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical ARBTT materialization is missing: {target}. "
            "Run python -m lynchpin.ingest.arbtt_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            yield ArbttFocusEvent(
                event_id=str(payload.get("event_id") or ""),
                timestamp=datetime.fromisoformat(str(payload["timestamp"])),
                duration_s=float(payload.get("duration_s") or 0),
                program=str(payload.get("program") or ""),
                title=str(payload.get("title") or ""),
                category=str(payload.get("category") or ""),
                tags=tuple(str(item) for item in payload.get("tags") or ()),
                project=str(payload["project"]) if payload.get("project") else None,
                source_path=str(payload.get("source_path") or ""),
                caveats=tuple(str(item) for item in payload.get("caveats") or ()),
            )


def daily_arbtt_activity(*, start: date, end: date) -> list[ArbttDayActivity]:
    by_day: dict[date, list[ArbttFocusEvent]] = defaultdict(list)
    for row in iter_arbtt_events():
        day = row.timestamp.date()
        if start <= day < end:
            by_day[day].append(row)
    return sorted(
        [
            ArbttDayActivity(
                date=day,
                active_minutes=sum(row.duration_s for row in rows) / 60.0,
                event_count=len(rows),
                program_count=len({row.program for row in rows if row.program}),
            )
            for day, rows in by_day.items()
        ],
        key=lambda row: row.date,
    )
