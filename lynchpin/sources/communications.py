"""Unified canonical communication-event reader."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

__all__ = [
    "CommunicationEvent",
    "CommunicationDayActivity",
    "communication_events_path",
    "communication_manifest_path",
    "iter_communication_events",
    "daily_communication_activity",
]


@dataclass(frozen=True)
class CommunicationEvent:
    event_id: str
    source: str
    account: str
    conversation_id: str
    timestamp: datetime | None
    direction: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    text_excerpt: str
    text_length: int
    media_count: int
    raw_kind: str
    raw_path: str
    confidence: str
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommunicationDayActivity:
    date: date
    event_count: int
    outbound_count: int
    conversation_count: int
    source_count: int


def communication_events_path(root: Path | None = None) -> Path:
    base = root or get_config().exports_root / "comms"
    return base / "processed/communication_events.ndjson"


def communication_manifest_path(root: Path | None = None) -> Path:
    return communication_events_path(root).with_suffix(".manifest.json")


def iter_communication_events(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[CommunicationEvent]:
    target = path or communication_events_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("communications", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical communication event materialization is missing: {target}. "
            "Run python -m lynchpin.ingest.communications_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            timestamp_raw = payload.get("timestamp")
            timestamp = datetime.fromisoformat(timestamp_raw) if isinstance(timestamp_raw, str) and timestamp_raw else None
            if start is not None or end is not None:
                if timestamp is None:
                    continue
                day = timestamp.date()
                if start is not None and day < start:
                    continue
                if end is not None and day >= end:
                    continue
            yield CommunicationEvent(
                event_id=str(payload.get("event_id") or ""),
                source=str(payload.get("source") or ""),
                account=str(payload.get("account") or ""),
                conversation_id=str(payload.get("conversation_id") or ""),
                timestamp=timestamp,
                direction=str(payload.get("direction") or "unknown"),
                sender=str(payload.get("sender") or ""),
                recipients=tuple(str(item) for item in payload.get("recipients") or ()),
                subject=str(payload.get("subject") or ""),
                text_excerpt=str(payload.get("text_excerpt") or ""),
                text_length=int(payload.get("text_length") or 0),
                media_count=int(payload.get("media_count") or 0),
                raw_kind=str(payload.get("raw_kind") or ""),
                raw_path=str(payload.get("raw_path") or ""),
                confidence=str(payload.get("confidence") or "unknown"),
                caveats=tuple(str(item) for item in payload.get("caveats") or ()),
            )


def daily_communication_activity(*, start: date, end: date, ensure: bool = True) -> list[CommunicationDayActivity]:
    by_day: dict[date, list[CommunicationEvent]] = defaultdict(list)
    for event in iter_communication_events(start=start, end=end, ensure=ensure):
        if event.timestamp is None:
            continue
        day = event.timestamp.date()
        by_day[day].append(event)
    out: list[CommunicationDayActivity] = []
    for day, rows in by_day.items():
        out.append(
            CommunicationDayActivity(
                date=day,
                event_count=len(rows),
                outbound_count=sum(1 for row in rows if row.direction == "outbound"),
                conversation_count=len({row.conversation_id for row in rows if row.conversation_id}),
                source_count=len(Counter(row.source for row in rows)),
            )
        )
    return sorted(out, key=lambda row: row.date)
