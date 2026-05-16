"""JSONL reader helpers for processed Samsung Health exports."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

PROCESSED = Path("/realm/data/exports/health/processed")


def load_jsonl(filename: str) -> Iterator[dict[str, Any]]:
    path = PROCESSED / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    yield payload


def in_range(d: date, start: Optional[date], end: Optional[date]) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True
