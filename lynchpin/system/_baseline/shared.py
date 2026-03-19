"""Shared helpers for baseline artifact rebuilds."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pandas import Series


def ensure_datetime(series: Series) -> Series:
    """Parse ISO8601 timestamps into timezone-aware pandas datetimes."""
    if series.empty:
        return series
    return pd.to_datetime(series, utc=True, errors="coerce")


def host_from_bucket(bucket: str, prefix: str) -> str:
    """Extract the host identifier after a known bucket prefix."""
    if not isinstance(bucket, str):
        return "unknown"
    if bucket.startswith(prefix):
        return bucket[len(prefix) :]
    if "_" in bucket:
        return bucket.split("_", 1)[-1]
    return bucket


def round_metric(value: float, digits: int = 3) -> float:
    """Round floats while avoiding negative zero artefacts."""
    rounded = round(float(value), digits)
    return 0.0 if math.isclose(rounded, 0.0, abs_tol=10 ** (-digits)) else rounded


def to_utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalise_repo_path(path: str) -> str:
    """Convert absolute repository paths into short identifiers."""
    if not isinstance(path, str):
        return "unknown"
    parts = Path(path).parts
    if "realm" in parts:
        idx = parts.index("realm")
        remaining = parts[idx + 1 :]
        return "/".join(remaining) if remaining else path.strip("/")
    return path.strip("/") or "unknown"


def categorise_command(cwd: Optional[str], command: str) -> str:
    """Map Atuin command rows onto coarse effort categories."""
    del command
    if not cwd or not isinstance(cwd, str):
        return "misc"
    path = cwd.strip()
    lowered = path.lower()
    if "project/sinex" in lowered or lowered.rstrip("/").endswith("sinex"):
        return "development:sinex"
    if "sinnix" in lowered:
        return "infrastructure:sinnix"
    if "/realm/project/" in lowered:
        return "development:other"
    if lowered.startswith("/realm/home") or lowered.startswith("/home"):
        return "home"
    return "misc"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def parse_timestamp(value: Optional[str], option_name: str) -> Optional[pd.Timestamp]:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Invalid timestamp for {option_name}: {value}")
    if isinstance(parsed, pd.DatetimeIndex):
        parsed = parsed[0]
    return pd.Timestamp(parsed)


def resolve_window(
    since: Optional[str],
    until: Optional[str],
    window_days: int,
    full: bool,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    until_ts = parse_timestamp(until, "--until") or pd.Timestamp.now(tz="UTC")
    if full and not since:
        return pd.Timestamp("1970-01-01T00:00:00Z"), until_ts

    since_ts = parse_timestamp(since, "--since") or (
        until_ts - pd.Timedelta(days=window_days)
    )
    if since_ts >= until_ts:
        raise ValueError("--since must be earlier than --until")
    return since_ts, until_ts
