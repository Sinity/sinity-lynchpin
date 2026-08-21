"""Persisted ActivityWatch products for graph-facing derived evidence."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

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
    "activitywatch_derived_generation_dir",
    "activitywatch_derived_manifest_path",
    "activitywatch_derived_path",
    "activitywatch_derived_product_paths",
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

_LOG = logging.getLogger(__name__)


def activitywatch_derived_dir(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "activitywatch/graph"


def activitywatch_derived_path(kind: str, root: Path | None = None) -> Path:
    """Return the legacy monolithic path for an explicit recovery read."""
    if kind not in PRODUCT_KINDS:
        raise ValueError(f"unknown ActivityWatch derived product kind: {kind}")
    return activitywatch_derived_dir(root) / f"{kind}.ndjson"


def activitywatch_derived_generation_dir(generation: str, root: Path | None = None) -> Path:
    """Return the immutable directory used by one derived-product generation."""
    return activitywatch_derived_dir(root) / "generations" / generation


def activitywatch_derived_manifest_path(root: Path | None = None) -> Path:
    return activitywatch_derived_dir(root) / "manifest.json"


def _derived_manifest(root: Path | None = None) -> dict[str, Any]:
    path = activitywatch_derived_manifest_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def activitywatch_derived_product_paths(kind: str, root: Path | None = None) -> dict[str, Path]:
    """Return the immutable logical-day paths serving one derived product."""
    if kind not in PRODUCT_KINDS:
        raise ValueError(f"unknown ActivityWatch derived product kind: {kind}")
    raw_paths = _derived_manifest(root).get("product_paths")
    if not isinstance(raw_paths, dict):
        return {}
    product_paths = raw_paths.get(kind)
    if not isinstance(product_paths, dict):
        return {}
    return {
        str(day): Path(str(path))
        for day, path in product_paths.items()
        if isinstance(path, str) and Path(path).exists()
    }


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
    paths = path or _product_paths_for_window(
        "focus_spans",
        start=logical_date(start),
        end=logical_date(end - timedelta(microseconds=1)),
        include_previous=True,
    )
    for row in _rows(paths):
        span = _focus_span(row)
        if span.end <= start_cmp or span.start >= end_cmp or span.duration_s < min_duration_s:
            continue
        yield span


def iter_derived_project_focus_days(
    *, start: datetime, end: datetime, path: Path | None = None, ensure: bool = True
) -> Iterator[ProjectFocusDay]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    first = logical_date(start)
    last = logical_date(end - timedelta(microseconds=1))
    paths = path or _product_paths_for_window("project_focus_days", start=first, end=last)
    for row in _dated_rows(paths, start=first, end=last):
        yield ProjectFocusDay(
            date=_date(row["date"]),
            project=str(row["project"]),
            duration_s=_float(row.get("duration_s")),
        )


def iter_derived_daily_activity(
    *, start: date, end: date, path: Path | None = None, ensure: bool = True
) -> Iterator[AWDayActivity]:
    _ensure_default_product(path, start=start, end=end, ensure=ensure)
    paths = path or _product_paths_for_window("daily_activity", start=start, end=end)
    for row in _dated_rows(paths, start=start, end=end):
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
    paths = path or _product_paths_for_window(
        "deep_work",
        start=logical_date(start),
        end=logical_date(end - timedelta(microseconds=1)),
        include_previous=True,
    )
    for row in _rows(paths):
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
    paths = path or _product_paths_for_window("circadian", start=start, end=end)
    for row in _dated_rows(paths, start=start, end=end):
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
    paths = path or _product_paths_for_window(
        "loops",
        start=logical_date(start),
        end=logical_date(end - timedelta(microseconds=1)),
        include_previous=True,
    )
    for row in _rows(paths):
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
    paths = path or _product_paths_for_window("fragmentation", start=start, end=end)
    for row in _dated_rows(paths, start=start, end=end):
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
    paths = path or _product_paths_for_window("attention", start=start, end=end)
    for row in _dated_rows(paths, start=start, end=end):
        yield AttentionMetrics(
            date=_date(row["date"]),
            entropy=_float(row.get("entropy")),
            gini=_float(row.get("gini")),
            top_project=_str_or_none(row.get("top_project")),
            project_count=_int(row.get("project_count")),
        )


def _product_paths_for_window(
    kind: str,
    *,
    start: date,
    end: date,
    include_previous: bool = False,
) -> tuple[Path, ...]:
    manifest = _derived_manifest()
    raw_products = manifest.get("product_paths")
    partitioned = isinstance(raw_products, dict) and isinstance(raw_products.get(kind), dict)
    paths = activitywatch_derived_product_paths(kind)
    if not partitioned:
        return (activitywatch_derived_path(kind),)
    first = start - timedelta(days=1) if include_previous else start
    return tuple(
        path
        for raw_day, path in sorted(paths.items())
        if first <= date.fromisoformat(raw_day) <= end
    )


def _rows(paths: Path | tuple[Path, ...]) -> Iterator[dict[str, object]]:
    candidates = (paths,) if isinstance(paths, Path) else paths
    if not candidates:
        return
    for path in candidates:
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


def _dated_rows(paths: Path | tuple[Path, ...], *, start: date, end: date) -> Iterator[dict[str, object]]:
    for row in _rows(paths):
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


def _focus_span(row: dict[str, object]) -> FocusSpan:
    start = _datetime(row["start"])
    end = _datetime(row["end"])
    if end < start:
        duration = _float(row.get("duration_s"), default=-1.0)
        if duration < 0:
            raise ValueError(
                "invalid persisted ActivityWatch focus span has no usable duration: "
                f"start={start.isoformat()} end={end.isoformat()}"
            )
        repaired_end = start + timedelta(seconds=duration)
        _LOG.warning(
            "repairing persisted ActivityWatch focus-span end from recorded duration: "
            "start=%s stored_end=%s duration_s=%s repaired_end=%s",
            start.isoformat(),
            end.isoformat(),
            duration,
            repaired_end.isoformat(),
        )
        end = repaired_end
    return FocusSpan(
        start=start,
        end=end,
        kind=str(row["kind"]),
        app=_str_or_none(row.get("app")),
        title=_str_or_none(row.get("title")),
        mode=_str_or_none(row.get("mode")),
        project=_str_or_none(row.get("project")),
        keypress_count=_int(row.get("keypress_count")),
        keylog_state=str(row.get("keylog_state") or "not_requested"),
    )


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
