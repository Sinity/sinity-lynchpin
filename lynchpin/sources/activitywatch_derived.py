"""Persisted ActivityWatch products for graph-facing derived evidence."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.parse import as_local
from ..core.primitives import logical_date
from .activitywatch_models import (
    AttentionMetrics,
    AWDayActivity,
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
    "iter_derived_daily_activity",
    "iter_derived_deep_work",
    "iter_derived_focus_spans",
    "iter_derived_fragmentation",
    "iter_derived_loops",
    "iter_derived_project_focus_days",
]

PRODUCT_KINDS = (
    "focus_spans",
    "daily_activity",
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
    start_cmp, end_cmp = as_local(start), as_local(end)
    for row in _rows(path or activitywatch_derived_path("focus_spans")):
        span = FocusSpan(
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            kind=str(row["kind"]),
            app=_str_or_none(row.get("app")),
            title=_str_or_none(row.get("title")),
            mode=_str_or_none(row.get("mode")),
            project=_str_or_none(row.get("project")),
            keypress_count=_int(row.get("keypress_count")),
            keylog_state=str(row.get("keylog_state") or "not_requested"),
        )
        if span.end <= start_cmp or span.start >= end_cmp or span.duration_s < min_duration_s:
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
            duration_s=_float(row.get("duration_s")),
        )


def iter_derived_daily_activity(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[AWDayActivity]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("daily_activity"), start=start, end=end):
        hourly = row.get("hourly_active") or ()
        yield AWDayActivity(
            date=_date(row["date"]),
            active_hours=_float(row.get("active_hours")),
            deep_work_min=_float(row.get("deep_work_min")),
            fragmentation_score=_float(row.get("fragmentation_score")),
            project_count=_int(row.get("project_count")),
            dominant_mode=_str_or_none(row.get("dominant_mode")),
            dominant_project=_str_or_none(row.get("dominant_project")),
            hourly_active=tuple(_float(h) for h in (hourly if isinstance(hourly, (list, tuple)) else ())),
            outage_hours=_float(row.get("outage_hours")),
            presence_active_hours=_float(row.get("presence_active_hours")),
            presence_typing_hours=_float(row.get("presence_typing_hours")),
            presence_data_gap_hours=_float(row.get("presence_data_gap_hours")),
        )


def iter_derived_deep_work(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[DeepWorkBlock]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    start_cmp, end_cmp = as_local(start), as_local(end)
    for row in _rows(path or activitywatch_derived_path("deep_work")):
        block = DeepWorkBlock(
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            duration_min=_float(row.get("duration_min")),
            project=_str_or_none(row.get("project")),
            mode=str(row.get("mode") or ""),
            focus_ratio=_float(row.get("focus_ratio")),
            app_switches=_int(row.get("app_switches")),
        )
        if block.end <= start_cmp or block.start >= end_cmp:
            continue
        yield block


def iter_derived_circadian(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[CircadianProfile]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("circadian"), start=start, end=end):
        yield CircadianProfile(
            date=_date(row["date"]),
            hour=_int(row.get("hour")),
            active_min=_float(row.get("active_min")),
            recovery_min=_float(row.get("recovery_min")),
            dominant_mode=_str_or_none(row.get("dominant_mode")),
            dominant_project=_str_or_none(row.get("dominant_project")),
        )


def iter_derived_loops(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[FocusLoop]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    start_cmp, end_cmp = as_local(start), as_local(end)
    for row in _rows(path or activitywatch_derived_path("loops")):
        loop = FocusLoop(
            date=_date(row["date"]),
            start=_datetime(row["start"]),
            end=_datetime(row["end"]),
            duration_min=_float(row.get("duration_min")),
            span_count=_int(row.get("span_count")),
            switch_count=_int(row.get("switch_count")),
            context_a=str(row.get("context_a") or ""),
            context_b=str(row.get("context_b") or ""),
            dominant_project=_str_or_none(row.get("dominant_project")),
        )
        if loop.end <= start_cmp or loop.start >= end_cmp:
            continue
        yield loop


def iter_derived_fragmentation(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[FragmentationMetrics]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("fragmentation"), start=start, end=end):
        yield FragmentationMetrics(
            date=_date(row["date"]),
            total_switches=_int(row.get("total_switches")),
            avg_focus_min=_float(row.get("avg_focus_min")),
            longest_focus_min=_float(row.get("longest_focus_min")),
            fragmentation=_float(row.get("fragmentation")),
        )


def iter_derived_attention(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[AttentionMetrics]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    for row in _dated_rows(path or activitywatch_derived_path("attention"), start=start, end=end):
        yield AttentionMetrics(
            date=_date(row["date"]),
            entropy=_float(row.get("entropy")),
            gini=_float(row.get("gini")),
            top_project=_str_or_none(row.get("top_project")),
            project_count=_int(row.get("project_count")),
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


def _int(value: object | None, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(str(value))


def _float(value: object | None, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    return float(str(value))
