"""JSONL reader helpers for processed Samsung Health exports."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.source import read_jsonl_with

PROCESSED = Path("/realm/data/exports/health/processed")


def load_jsonl(filename: str) -> Iterator[dict[str, Any]]:
    yield from read_jsonl_with(PROCESSED / filename, lambda p: p, source_name=filename)


def in_range(d: date, start: Optional[date], end: Optional[date]) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True
