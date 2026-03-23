from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable, Iterator, Optional, Sequence, Tuple


@dataclass(frozen=True)
class WarehouseContext:
    limit: Optional[int]
    since: Optional[datetime]
    until: Optional[datetime]
    start_date: Optional[str]
    end_date: Optional[str]


@dataclass(frozen=True)
class TableSpec:
    name: str
    create_sql: str
    insert_sql: str
    rows: Callable[[WarehouseContext], Iterator[Tuple]]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    tables: Sequence[TableSpec]


def _parse_dt(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time()).replace(tzinfo=timezone.utc)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_ts(dt: Optional[datetime]) -> Optional[datetime]:
    """Strip timezone info from datetime for consistent warehouse storage."""
    if dt is not None and hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, default=str)


def _maybe_limit(iterator: Iterable, limit: Optional[int]) -> Iterator:
    if limit is None:
        yield from iterator
        return
    count = 0
    for item in iterator:
        if count >= limit:
            break
        count += 1
        yield item
