"""Persisted ActivityWatch products for graph-facing derived evidence."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.primitives import logical_date
from .activitywatch_models import (
    AttentionMetrics,
    CircadianProfile,
    DeepWorkBlock,
    FocusLoop,
    FocusSpan,
    FragmentationMetrics,
    ProjectFocusDay,
)

__all__ = [
    "activitywatch_derived_dir",
    "activitywatch_derived_manifest_path",
    "activitywatch_derived_path",
    "iter_derived_attention",
    "iter_derived_circadian",
    "iter_derived_deep_work",
    "iter_derived_focus_spans",
    "iter_derived_fragmentation",
    "iter_derived_loops",
    "iter_derived_project_focus_days",
]

PRODUCT_KINDS = (
    "focus_spans",
    "project_focus_days",
    "deep_work",
    "circadian",
    "loops",
    "fragmentation",
    "attention",
)


def activitywatch_derived_dir(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "activitywatch/graph"


def activitywatch_derived_path(kind: str, root: Path | None = None) -> Path:
    if kind not in PRODUCT_KINDS:
        raise ValueError(f"unknown ActivityWatch derived product kind: {kind}")
    return activitywatch_derived_dir(root) / f"{kind}.ndjson"


def activitywatch_derived_manifest_path(root: Path | None = None) -> Path:
    return activitywatch_derived_dir(root) / "manifest.json"


def iter_derived_focus_spans(
    *,
    start: datetime,
    end: datetime,
    min_duration_s: float = 0.0,
    path: Path | None = None,
    ensure: bool = True,
) -> Iterator[FocusSpan]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _rows(path or activitywatch_derived_path("focus_spans")):
        span = FocusSpan(
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            kind=str(row["kind"]),
            app=_str_or_none(row.get("app")),
            title=_str_or_none(row.get("title")),
            mode=_str_or_none(row.get("mode")),
            project=_str_or_none(row.get("project")),
            keypress_count=int(row.get("keypress_count") or 0),
            keylog_state=str(row.get("keylog_state") or "not_requested"),
        )
        if span.end <= start or span.start >= end or span.duration_s < min_duration_s:
            continue
        yield span


def iter_derived_project_focus_days(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[ProjectFocusDay]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    first = logical_date(start)
    last = logical_date(end - timedelta(microseconds=1))
    for row in _dated_rows(path or activitywatch_derived_path("project_focus_days"), start=first, end=last):
        yield ProjectFocusDay(
            date=_date(row["date"]),
            project=str(row["project"]),
            duration_s=float(row.get("duration_s") or 0.0),
        )


def iter_derived_deep_work(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[DeepWorkBlock]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _rows(path or activitywatch_derived_path("deep_work")):
        block = DeepWorkBlock(
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            duration_min=float(row.get("duration_min") or 0.0),
            project=_str_or_none(row.get("project")),
            mode=str(row.get("mode") or ""),
            focus_ratio=float(row.get("focus_ratio") or 0.0),
            app_switches=int(row.get("app_switches") or 0),
        )
        if block.end <= start or block.start >= end:
            continue
        yield block


def iter_derived_circadian(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[CircadianProfile]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("circadian"), start=start, end=end):
        yield CircadianProfile(
            date=_date(row["date"]),
            hour=int(row.get("hour") or 0),
            active_min=float(row.get("active_min") or 0.0),
            recovery_min=float(row.get("recovery_min") or 0.0),
            dominant_mode=_str_or_none(row.get("dominant_mode")),
            dominant_project=_str_or_none(row.get("dominant_project")),
        )


def iter_derived_loops(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[FocusLoop]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _rows(path or activitywatch_derived_path("loops")):
        loop = FocusLoop(
            date=_date(row["date"]),
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            duration_min=float(row.get("duration_min") or 0.0),
            span_count=int(row.get("span_count") or 0),
            switch_count=int(row.get("switch_count") or 0),
            context_a=str(row.get("context_a") or ""),
            context_b=str(row.get("context_b") or ""),
            dominant_project=_str_or_none(row.get("dominant_project")),
        )
        if loop.end <= start or loop.start >= end:
            continue
        yield loop


def iter_derived_fragmentation(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[FragmentationMetrics]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("fragmentation"), start=start, end=end):
        yield FragmentationMetrics(
            date=_date(row["date"]),
            total_switches=int(row.get("total_switches") or 0),
            avg_focus_min=float(row.get("avg_focus_min") or 0.0),
            longest_focus_min=float(row.get("longest_focus_min") or 0.0),
            fragmentation=float(row.get("fragmentation") or 0.0),
        )


def iter_derived_attention(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[AttentionMetrics]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("attention"), start=start, end=end):
        yield AttentionMetrics(
            date=_date(row["date"]),
            entropy=float(row.get("entropy") or 0.0),
            gini=float(row.get("gini") or 0.0),
            top_project=_str_or_none(row.get("top_project")),
            project_count=int(row.get("project_count") or 0),
        )


def _rows(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(
            f"ActivityWatch derived product is missing: {path}. "
            "Run python -m lynchpin.ingest.activitywatch_derived_materialize."
        )
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield payload


def _dated_rows(path: Path, *, start: date, end: date) -> Iterator[dict[str, object]]:
    for row in _rows(path):
        row_date = _date(row["date"])
        if start <= row_date <= end:
            yield row


def _ensure_default_product(
    path: Path | None,
    *,
    start: date | datetime,
    end: date | datetime,
    ensure: bool,
) -> None:
    if path is not None or not ensure:
        return
    from ..materialization import ensure_materialized

    if isinstance(start, datetime) and isinstance(end, datetime):
        window = _datetime_window(start, end)
    else:
        start_date = _date(start)
        window = (start_date, _date(end) + timedelta(days=1))
    ensure_materialized("activitywatch_derived", window=window)


def _datetime_window(start: datetime, end: datetime) -> tuple[date, date]:
    end_date = end.date()
    if (end.hour, end.minute, end.second, end.microsecond) != (0, 0, 0, 0):
        end_date += timedelta(days=1)
    return (start.date(), end_date)


def _date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _str_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
