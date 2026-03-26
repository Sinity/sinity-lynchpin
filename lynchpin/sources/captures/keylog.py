from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config
from ...signals import _as_local

_CACHE_LOGGER = logging.getLogger(__name__ + ".cachew")
if _CACHE_LOGGER.level == logging.NOTSET:
    _CACHE_LOGGER.setLevel(logging.WARNING)


@dataclass(frozen=True)
class KeylogEvent:
    timestamp: datetime
    event: str
    session: str | None
    window: str | None
    keycode: str | None
    changed: bool


@dataclass(frozen=True)
class KeylogPress:
    timestamp: datetime
    changed: bool


def iter_key_events(
    *,
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> Iterator[KeylogEvent]:
    start_local = _as_local(start)
    end_local = _as_local(end)
    current = start_local.date()
    scan_root = _resolve_root(root)
    while current <= end_local.date():
        path = _log_path(current, scan_root)
        if path.exists():
            for event in load_keylog_day(current, root=scan_root):
                if start_local <= event.timestamp < end_local:
                    yield event
        current += timedelta(days=1)


def iter_key_presses(
    *,
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> Iterator[KeylogPress]:
    start_local = _as_local(start)
    for timestamp_us, changed in iter_key_press_samples(start=start, end=end, root=root):
        yield KeylogPress(
            timestamp=datetime.fromtimestamp(timestamp_us / 1_000_000, tz=start_local.tzinfo),
            changed=changed,
        )


def iter_key_press_samples(
    *,
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> Iterator[tuple[int, bool]]:
    start_local = _as_local(start)
    end_local = _as_local(end)
    start_us = _datetime_to_epoch_us(start_local)
    end_us = _datetime_to_epoch_us(end_local)
    current = start_local.date()
    scan_root = _resolve_root(root)
    while current <= end_local.date():
        path = _log_path(current, scan_root)
        if path.exists():
            for event_us, changed in load_keylog_press_samples_day(current, root=scan_root):
                if start_us <= event_us < end_us:
                    yield event_us, changed
        current += timedelta(days=1)


def keylog_coverage_by_date(
    *,
    start: date,
    end: date,
    root: Path | None = None,
) -> dict[date, bool]:
    scan_root = _resolve_root(root)
    coverage: dict[date, bool] = {}
    current = start
    while current <= end:
        coverage[current] = _log_path(current, scan_root).exists()
        current += timedelta(days=1)
    return coverage


@persistent_cache(
    "keylog_day",
    depends_on=lambda day, root=None: file_signature(_log_path(day, _resolve_root(root))),
    logger=_CACHE_LOGGER,
)
def load_keylog_day(
    day: date,
    *,
    root: Path | None = None,
) -> list[KeylogEvent]:
    path = _log_path(day, _resolve_root(root))
    if not path.exists():
        return []

    events: list[KeylogEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            timestamp = _parse_timestamp(payload.get("ts"))
            if timestamp is None:
                continue
            events.append(KeylogEvent(
                timestamp=timestamp,
                event=str(payload.get("event") or ""),
                session=_optional_str(payload.get("session")),
                window=_optional_str(payload.get("window")),
                keycode=_optional_str(payload.get("keycode")),
                changed=bool(payload.get("changed", False)),
            ))
    return events


@persistent_cache(
    "keylog_presses_day",
    depends_on=lambda day, root=None: file_signature(_log_path(day, _resolve_root(root))),
    logger=_CACHE_LOGGER,
)
def load_keylog_press_samples_day(
    day: date,
    *,
    root: Path | None = None,
) -> list[tuple[int, bool]]:
    path = _log_path(day, _resolve_root(root))
    if not path.exists():
        return []

    events: list[tuple[int, bool]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if str(payload.get("event") or "") != "press":
                continue
            timestamp = _parse_timestamp(payload.get("ts"))
            if timestamp is None:
                continue
            events.append((_datetime_to_epoch_us(timestamp), bool(payload.get("changed", False))))
    return events


def _resolve_root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    return get_config().keylog_root


def _log_path(day: date, root: Path) -> Path:
    return root / "logs" / f"{day.isoformat()}.jsonl"


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_local(datetime.fromisoformat(text))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _datetime_to_epoch_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)
