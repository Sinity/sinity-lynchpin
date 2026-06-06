"""Materialization/run report artifacts for DAG executions."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from lynchpin.analysis.core.dag import StepResult, StepStatus
from lynchpin.core.io import save_json


def materialization_report_payload(
    *,
    dag_name: str,
    results: Iterable[StepResult],
    up_to: str | None,
    materialization_plan: Iterable[Any] | None = None,
) -> dict[str, Any]:
    rows = [_step_payload(row) for row in results]
    by_status: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dag_name": dag_name,
        "up_to": up_to,
        "step_count": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "elapsed_seconds": round(sum(float(row.get("elapsed_seconds") or 0.0) for row in rows), 3),
        "steps": rows,
        "source_statuses": _source_statuses(rows),
        "caveats": [
            "row_counts are extracted from step return values when exposed by the producer",
            "dry-run plans are not written as materialization reports",
        ],
    }
    if materialization_plan is not None:
        payload["materialization_plan"] = [_jsonish(row) for row in materialization_plan]
    return payload


def write_materialization_report(
    out: Path,
    *,
    dag_name: str,
    results: Iterable[StepResult],
    up_to: str | None = None,
    materialization_plan: Iterable[Any] | None = None,
) -> dict[str, Any]:
    payload = materialization_report_payload(
        dag_name=dag_name,
        results=results,
        up_to=up_to,
        materialization_plan=materialization_plan,
    )
    save_json(out, payload, sort_keys=True)
    return payload


def _step_payload(result: StepResult) -> dict[str, Any]:
    value = _jsonish(result.result)
    payload = {
        "name": result.name,
        "status": result.status.value if isinstance(result.status, StepStatus) else str(result.status),
        "elapsed_seconds": result.elapsed_seconds,
        "error": result.error,
        "row_counts": _row_counts(value),
        "degraded_reasons": _degraded_reasons(value, result.error),
    }
    source_statuses = _extract_source_statuses(value)
    if source_statuses:
        payload["source_statuses"] = source_statuses
    return payload


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonish(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish(item) for item in value]
    if hasattr(value, "to_dict"):
        return _jsonish(value.to_dict())
    if is_dataclass(value):
        return _jsonish(asdict(value))
    return str(value)


def _row_counts(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(value, dict):
        return counts
    nested = value.get("counts")
    if isinstance(nested, dict):
        for key, raw in nested.items():
            if isinstance(raw, int):
                counts[str(key)] = raw
    for key, raw in value.items():
        if key.endswith("_count") and isinstance(raw, int):
            counts[key] = raw
    return dict(sorted(counts.items()))


def _degraded_reasons(value: Any, error: str | None) -> list[str]:
    reasons = []
    if error:
        reasons.append(error.splitlines()[0])
    if isinstance(value, dict):
        status = value.get("status")
        reason = value.get("reason")
        if status not in (None, "ok", "stable", "success") and reason:
            reasons.append(str(reason))
        for source in value.get("source_statuses", []) if isinstance(value.get("source_statuses"), list) else []:
            if isinstance(source, dict) and source.get("status") not in (None, "ok"):
                text = source.get("reason") or source.get("source") or source.get("status")
                reasons.append(str(text))
    return sorted(dict.fromkeys(reasons))


def _extract_source_statuses(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("source_statuses"), list):
        return []
    rows = []
    for row in value["source_statuses"]:
        if isinstance(row, dict):
            rows.append({
                "source": row.get("source"),
                "kind": row.get("kind"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "row_count": row.get("row_count"),
            })
    return rows


def _source_statuses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        for status in row.get("source_statuses", []):
            if isinstance(status, dict):
                result.append({"step": row["name"], **status})
    return result


__all__ = [
    "materialization_report_payload",
    "write_materialization_report",
]
