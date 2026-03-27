from __future__ import annotations

from datetime import datetime
from typing import Sequence


def _render_counter(counter: Sequence[Sequence[object]], limit: int = 12) -> str:
    items = []
    for key, value in counter[:limit]:
        items.append(f"{key} {value}")
    return ", ".join(items)


def _month_start(month_key: str, tzinfo) -> datetime:
    year, month = (int(part) for part in month_key.split("-", 1))
    return datetime(year, month, 1, tzinfo=tzinfo)


def _month_after(month_key: str, tzinfo) -> datetime:
    year, month = (int(part) for part in month_key.split("-", 1))
    if month == 12:
        return datetime(year + 1, 1, 1, tzinfo=tzinfo)
    return datetime(year, month + 1, 1, tzinfo=tzinfo)


def _counter_pairs(counter: Sequence[Sequence[object]], *, divisor: float) -> list[tuple[str, float | int]]:
    pairs: list[tuple[str, float | int]] = []
    for item in counter:
        if len(item) < 2:
            continue
        label = str(item[0])
        value = float(item[1] or 0.0)
        if divisor == 1.0:
            pairs.append((label, int(value)))
        else:
            pairs.append((label, round(value / divisor, 2)))
    return pairs


def _counter_mapping(counter: Sequence[Sequence[object]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for item in counter:
        if len(item) < 2:
            continue
        mapping[str(item[0])] = int(item[1] or 0)
    return mapping
