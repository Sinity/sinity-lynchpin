"""Shared private helpers for machine MCP tool modules."""
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    json_safe as _json_safe,
)


def _ensure_machine_materialized_for_read(
    *,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """Ensure canonical machine telemetry before reading promoted tables."""

    from lynchpin.materialization import ensure_materialized

    window = (start, end) if start is not None and end is not None else None
    return ensure_materialized("machine", window=window).to_json()


def _exclusive_end(end: Any) -> Any:
    return end + timedelta(days=1) if end is not None else None


def _parse_temporal_bound(value: str | None) -> date | datetime | None:
    """Parse an MCP date/datetime bound.

    Date-only inputs keep the existing day-granularity behavior. Datetime
    inputs are preserved so machine telemetry tools can answer exact recent
    windows such as "last few hours".
    """
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    if "T" in normalized or " " in normalized:
        return datetime.fromisoformat(normalized)
    return date.fromisoformat(normalized)


def _materialization_window_for_bounds(
    start: date | datetime | None,
    end: date | datetime | None,
) -> tuple[date, date] | None:
    """Return the date window needed to materialize a temporal read window."""
    if start is None or end is None:
        return None
    start_day = start.date() if isinstance(start, datetime) else start
    end_day = end.date() if isinstance(end, datetime) else end
    return (start_day, _exclusive_end(end_day))


def _ensure_work_observation_substrate_for_read(
    *,
    caller: str,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    # Use late import to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    window = (start, end) if start is not None and end is not None else None
    return machine_module.ensure_substrate_materialized_for_read(caller=caller, window=window)


def _analysis_artifact(name: str) -> dict[str, Any] | None:
    from lynchpin.core.io import load_materialized_analysis_artifact

    payload, _materialization = load_materialized_analysis_artifact(name)
    return payload if isinstance(payload, dict) else None


def _required_analysis_artifact(name: str) -> dict[str, Any]:
    from lynchpin.core.io import resolve_analysis_path

    path = Path(resolve_analysis_path(name))
    payload = _analysis_artifact(name)
    if payload is None:
        raise FileNotFoundError(
            f"required machine analysis artifact is missing or malformed: {path}"
        )
    return payload


def _timestamp_filter(
    row: dict[str, Any],
    *,
    start: str | None,
    end: str | None,
    start_key: str,
    end_key: str,
) -> bool:
    if not start and not end:
        return True
    row_start = str(row.get(start_key) or "")
    row_end = str(row.get(end_key) or "")
    row_day_start = row_start[:10]
    row_day_end = row_end[:10] or row_day_start
    if start and row_day_end < start:
        return False
    if end and row_day_start > end:
        return False
    return True


def _artifact_rows(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if payload is None:
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _workflow_mechanics_artifact_payload(
    *,
    start: str | None,
    end: str | None,
    project: str | None,
    refresh_id: str | None,
    retry_gap_min: int,
    limit: int,
) -> dict[str, Any] | None:
    if (
        start is not None
        or end is not None
        or project is not None
        or refresh_id is not None
        or retry_gap_min != 20
        or limit != 100
    ):
        return None

    payload = _analysis_artifact("workflow_mechanics.json")
    if payload is None:
        return None
    if payload.get("start") is not None or payload.get("end") is not None:
        return None
    return _json_safe({**payload, "source": "artifact"})


def _round(value: Any, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _float_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return round(values[midpoint], 3)
    return round((values[midpoint - 1] + values[midpoint]) / 2, 3)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)


def _best_refresh_or_none(conn: Any, table: str) -> str | None:
    try:
        return best_materialized_refresh_id(conn, table, caller=f"machine_gap_summary.{table}")
    except Exception:
        return None
