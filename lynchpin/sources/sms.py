"""SMS source — Samsung GDPR text message export.

Data: /realm/data/exports/samsung/processed/2026-03-30-gdpr-extracted/
      samsungcloud_gk000066110887_20260329_access/SMS/

The Samsung SMS export format wraps each message in a CSV row whose VALUE
column contains a JSON object with double-escaping (CSV "" + JSON \" = \\"
after CSV parsing). This module handles the unescaping.

Coverage: 1,790 messages across 2021-2025. Sparse (~1/day average) but
covers a communication channel not present in Messenger, Reddit, or Wykop.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..core.errors import SourceUnavailableError

SMS_ROOT = Path(
    "/realm/data/exports/samsung/processed/2026-03-30-gdpr-extracted/"
    "samsungcloud_gk000066110887_20260329_access/SMS"
)

# type values from Samsung SMS schema
_TYPE_LABELS = {"1": "received", "2": "sent", "3": "draft"}


@dataclass(frozen=True)
class SMSMessage:
    """One SMS message."""

    msg_id: int
    thread_id: int
    address: str  # phone number or sender label (e.g. "SklepVTP")
    date: datetime
    body: str
    msg_type: str  # "received" | "sent" | "draft"
    read: bool
    seen: bool

    @property
    def is_sent(self) -> bool:
        return self.msg_type == "sent"

    @property
    def is_received(self) -> bool:
        return self.msg_type == "received"

    @property
    def body_length(self) -> int:
        return len(self.body)


@dataclass(frozen=True)
class SMSThread:
    """Summary of one conversation thread."""

    thread_id: int
    counterpart: str  # primary address
    message_count: int
    sent_count: int
    received_count: int
    first_date: datetime
    last_date: datetime
    total_chars: int


@dataclass(frozen=True)
class SMSDayActivity:
    """Per-day SMS activity."""

    date: str  # YYYY-MM-DD
    sent_count: int
    received_count: int
    sent_chars: int
    received_chars: int
    counterpart_count: int  # unique contacts


def _parse_sms_csv(root: Optional[Path] = None) -> Iterator[dict[str, object]]:
    """Parse the Samsung SMS CSV format, yielding raw message dicts."""
    base = root or SMS_ROOT
    if not base.exists():
        return

    for fn in sorted(base.glob("SMS_*.csv")):
        with open(fn, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                val = row.get("VALUE", "")
                if not val:
                    continue
                val = val.strip().strip('"')
                # Samsung double-escaping: CSV "" + JSON \" → \\" in parsed string
                val = val.replace('\\"', '"')
                try:
                    yield json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    continue


def iter_messages(root: Optional[Path] = None) -> Iterator[SMSMessage]:
    """Iterate all SMS messages in chronological order."""
    messages = []
    for raw in _parse_sms_csv(root):
        try:
            ts = int(str(raw.get("date", 0))) / 1000
            msg_type = str(raw.get("type", "1"))
            messages.append(
                SMSMessage(
                    msg_id=int(str(raw.get("_id", 0))),
                    thread_id=int(str(raw.get("thread_id", 0))),
                    address=str(raw.get("address", "")),
                    date=datetime.fromtimestamp(ts, tz=timezone.utc),
                    body=str(raw.get("body", "")),
                    msg_type=_TYPE_LABELS.get(msg_type, f"unknown({msg_type})"),
                    read=raw.get("read", "1") == "1",
                    seen=raw.get("seen", "1") == "1",
                )
            )
        except (ValueError, KeyError, OSError):
            continue

    yield from sorted(messages, key=lambda m: m.date)


def thread_summaries(root: Optional[Path] = None) -> list[SMSThread]:
    """Aggregate messages into per-thread summaries, ranked by message count."""
    threads: dict[int, list[SMSMessage]] = defaultdict(list)
    for msg in iter_messages(root):
        threads[msg.thread_id].append(msg)

    result = []
    for tid, msgs in threads.items():
        msgs.sort(key=lambda m: m.date)
        counterparts = set(m.address for m in msgs)
        primary = max(counterparts, key=lambda c: sum(1 for m in msgs if m.address == c))
        result.append(
            SMSThread(
                thread_id=tid,
                counterpart=primary,
                message_count=len(msgs),
                sent_count=sum(1 for m in msgs if m.is_sent),
                received_count=sum(1 for m in msgs if m.is_received),
                first_date=msgs[0].date,
                last_date=msgs[-1].date,
                total_chars=sum(m.body_length for m in msgs),
            )
        )

    return sorted(result, key=lambda t: -t.message_count)


def daily_activity(
    start: Optional[str] = None,
    end: Optional[str] = None,
    root: Optional[Path] = None,
) -> list[SMSDayActivity]:
    """Per-day SMS activity summary."""
    buckets: dict = defaultdict(  # type: ignore[type-arg]
        lambda: {
            "sent_count": 0,
            "received_count": 0,
            "sent_chars": 0,
            "received_chars": 0,
            "counterparts": set(),
        }
    )

    for msg in iter_messages(root):
        day = msg.date.strftime("%Y-%m-%d")
        if start and day < start:
            continue
        if end and day > end:
            continue
        b = buckets[day]
        b["counterparts"].add(msg.address)
        if msg.is_sent:
            b["sent_count"] += 1
            b["sent_chars"] += msg.body_length
        else:
            b["received_count"] += 1
            b["received_chars"] += msg.body_length

    result = []
    for day in sorted(buckets):
        b = buckets[day]
        result.append(
            SMSDayActivity(
                date=day,
                sent_count=b["sent_count"],
                received_count=b["received_count"],
                sent_chars=b["sent_chars"],
                received_chars=b["received_chars"],
                counterpart_count=len(b["counterparts"]),
            )
        )
    return result


def date_range(root: Optional[Path] = None) -> tuple[datetime, datetime]:
    """Oldest and newest message dates."""
    messages = list(iter_messages(root))
    if not messages:
        raise SourceUnavailableError("sms", reason="No SMS messages found")
    return messages[0].date, messages[-1].date


def counterpart_stats(root: Optional[Path] = None) -> list[tuple[str, int]]:
    """Message count per counterpart (address)."""
    counts: dict[str, int] = defaultdict(int)
    for msg in iter_messages(root):
        counts[msg.address] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


__all__ = [
    "SMSMessage",
    "SMSThread",
    "SMSDayActivity",
    "iter_messages",
    "thread_summaries",
    "daily_activity",
    "date_range",
    "counterpart_stats",
]
