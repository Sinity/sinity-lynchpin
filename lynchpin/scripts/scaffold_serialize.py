"""Serialization helpers for scaffold generator output.

Converts dataclasses, datetimes, dates, enums, and nested structures
into JSON-safe dicts. Used by generate_scaffold.py.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


def to_dict(obj: Any) -> Any:
    """Recursively convert an object to JSON-safe types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {_key(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, frozenset, set)):
        return [to_dict(item) for item in obj]
    # Fallback: str()
    return str(obj)


def _key(k: Any) -> str:
    """Ensure dict keys are strings."""
    if isinstance(k, str):
        return k
    if isinstance(k, (date, datetime)):
        return k.isoformat()
    if isinstance(k, tuple):
        return "_".join(str(x) for x in k)
    return str(k)


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write data as JSON, converting via to_dict first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(to_dict(data), f, indent=indent, default=str, ensure_ascii=False)
        f.write("\n")
