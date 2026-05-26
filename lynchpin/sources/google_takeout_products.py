"""Canonical typed Google Takeout product rows."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config

__all__ = [
    "google_takeout_products_dir",
    "GoogleTakeoutDay",
    "GoogleTakeoutEvent",
    "iter_assets",
    "iter_calendar",
    "iter_contacts",
    "iter_daily_activity",
    "iter_events",
    "iter_gmail",
    "iter_keep_notes",
    "iter_my_activity",
    "iter_play_store",
    "iter_purchases",
    "iter_tasks",
    "iter_youtube",
]


@dataclass(frozen=True)
class GoogleTakeoutEvent:
    product: str
    timestamp: datetime
    title: str
    service: str | None
    source_member: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class GoogleTakeoutDay:
    date: date
    product: str
    service: str | None
    event_count: int


def google_takeout_products_dir() -> Path:
    return get_config().exports_root / "google/processed/takeout-products"


def iter_contacts() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("contacts.ndjson")


def iter_keep_notes() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("keep_notes.ndjson")


def iter_my_activity() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("my_activity.ndjson")


def iter_play_store() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("play_store.ndjson")


def iter_purchases() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("purchases.ndjson")


def iter_tasks() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("tasks.ndjson")


def iter_youtube() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("youtube.ndjson")


def iter_gmail() -> Iterator[dict[str, Any]]:
    """Yield Gmail messages as dict rows from Takeout .mbox archives.

    Delegates to ``gmail_takeout.iter_gmail_messages_deduped`` and
    serialises each ``GmailMessage`` to a dict for compatibility with
    the existing ``iter_events`` pattern.
    """
    from .gmail_takeout import GmailMessage, iter_gmail_messages_deduped

    for msg in iter_gmail_messages_deduped():
        if msg.timestamp is None:
            continue
        yield {
            "product": "gmail",
            "timestamp": msg.timestamp.isoformat(),
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "sender": msg.sender,
            "recipients": list(msg.recipients),
            "cc": list(msg.cc),
            "subject": msg.subject,
            "body_preview": msg.body_preview,
            "label": msg.label,
            "archive_source": msg.archive_source,
            "size_bytes": msg.size_bytes,
        }


def iter_assets() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("assets.ndjson")


def iter_calendar() -> Iterator[dict[str, Any]]:
    """Yield Google Calendar events from the processed extract.

    Lives one directory up from the per-product NDJSONs (calendar.jsonl
    at the processed root) because the takeout extractor emits it
    separately. Tolerates absence — empty iterator when no calendar
    archive has been ingested yet.
    """
    path = get_config().exports_root / "google/processed/calendar.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_events(product: str | None = None) -> Iterator[GoogleTakeoutEvent]:
    """Yield timestamped event-like Google Takeout product rows.

    Asset inventories and contacts are intentionally excluded: they are useful
    as provenance/file records but not personal activity events.
    """
    # NOTE: youtube.ndjson holds video metadata (title, id, category) without
    # watch timestamps, so iter_youtube has no time signal. Subscribed-to
    # videos and watch history would need a separate timed extractor that
    # the takeout exporter doesn't produce. Excluded from iter_events.
    product_iterators = {
        "calendar": iter_calendar,
        "keep_notes": iter_keep_notes,
        "my_activity": iter_my_activity,
        "play_store": iter_play_store,
        "purchases": iter_purchases,
        "tasks": iter_tasks,
    }
    for product_name, iterator in product_iterators.items():
        if product is not None and product_name != product:
            continue
        for payload in iterator():
            stamp = _event_timestamp(product_name, payload)
            if stamp is None:
                continue
            yield GoogleTakeoutEvent(
                product=product_name,
                timestamp=stamp,
                title=_event_title(product_name, payload),
                service=_event_service(product_name, payload),
                source_member=str(payload.get("source_member") or ""),
                payload=payload,
            )


def iter_daily_activity(
    start: date | None = None,
    end: date | None = None,
) -> Iterator[GoogleTakeoutDay]:
    counts: dict[tuple[date, str, str | None], int] = {}
    for event in iter_events():
        day = event.timestamp.date()
        if start and day < start:
            continue
        if end and day >= end:
            continue
        key = (day, event.product, event.service)
        counts[key] = counts.get(key, 0) + 1
    for day, product, service in sorted(counts, key=lambda key: (key[0], key[1], key[2] or "")):
        yield GoogleTakeoutDay(
            date=day,
            product=product,
            service=service,
            event_count=counts[(day, product, service)],
        )


def _iter_rows(name: str) -> Iterator[dict[str, Any]]:
    path = google_takeout_products_dir() / name
    if not path.exists():
        raise FileNotFoundError(
            f"canonical Google Takeout product materialization is missing: {path}. "
            "Run `python -m lynchpin.ingest.google_takeout_products`."
        )
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield payload


def _event_timestamp(product: str, payload: dict[str, Any]) -> datetime | None:
    keys_by_product = {
        "calendar": ("start_at", "created_at"),
        "keep_notes": ("created_at", "edited_at"),
        "my_activity": ("timestamp", "timestamp_text"),
        "play_store": ("created_at",),
        "purchases": ("created_at",),
        "tasks": ("completed_at", "updated_at", "created_at", "due_at"),
    }
    for key in keys_by_product.get(product, ()):
        stamp = _parse_timestamp(payload.get(key))
        if stamp is not None:
            return stamp
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    normalized = raw.replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError:
        stamp = _parse_takeout_activity_timestamp(raw)
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


def _parse_takeout_activity_timestamp(raw: str) -> datetime | None:
    # Examples: "Aug 7, 2019, 4:34:48 PM UTC" and
    # "Jan 1, 2026, 1:00:00 PM UTC".
    normalized = re.sub(r"\s+", " ", raw.replace("\xa0", " ")).strip()
    for fmt in ("%b %d, %Y, %I:%M:%S %p UTC", "%B %d, %Y, %I:%M:%S %p UTC"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _event_title(product: str, payload: dict[str, Any]) -> str:
    if product == "tasks":
        title = payload.get("title") or payload.get("notes")
    elif product == "purchases":
        names = payload.get("item_names")
        title = payload.get("merchant") or (", ".join(str(item) for item in names if item) if isinstance(names, list) else None)
    elif product == "calendar":
        title = payload.get("summary") or payload.get("description")
    elif product == "youtube":
        title = payload.get("title") or payload.get("video_title")
    else:
        title = payload.get("title") or payload.get("text")
    return str(title or "").strip()


def _event_service(product: str, payload: dict[str, Any]) -> str | None:
    if product == "my_activity":
        service = payload.get("service")
    elif product == "play_store":
        service = payload.get("category")
    else:
        service = None
    return str(service).strip() if service else None
