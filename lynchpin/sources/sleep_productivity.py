"""Canonical derived sleep-to-productivity product."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

from ..core.config import get_config


@dataclass(frozen=True)
class SleepProductivityRow:
    sleep_date: date
    sleep_hours: float
    sleep_score: float | None
    sleep_quality: str
    workday_active_hours: float
    workday_deep_work_min: float
    productivity_vs_baseline: float


def sleep_productivity_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "sleep/productivity.ndjson"


def sleep_productivity_manifest_path(root: Path | None = None) -> Path:
    return sleep_productivity_path(root).with_suffix(".manifest.json")


def iter_sleep_productivity(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[SleepProductivityRow]:
    target = path or sleep_productivity_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("sleep_productivity", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical sleep-productivity product is missing: {target}. "
            "Run python -m lynchpin.ingest.sleep_productivity_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            sleep_date = date.fromisoformat(str(payload["sleep_date"]))
            if start is not None and sleep_date < start:
                continue
            if end is not None and sleep_date >= end:
                continue
            raw_score = payload.get("sleep_score")
            yield SleepProductivityRow(
                sleep_date=sleep_date,
                sleep_hours=float(payload.get("sleep_hours") or 0.0),
                sleep_score=float(raw_score) if raw_score is not None else None,
                sleep_quality=str(payload.get("sleep_quality") or "unknown"),
                workday_active_hours=float(payload.get("workday_active_hours") or 0.0),
                workday_deep_work_min=float(payload.get("workday_deep_work_min") or 0.0),
                productivity_vs_baseline=float(payload.get("productivity_vs_baseline") or 0.0),
            )
