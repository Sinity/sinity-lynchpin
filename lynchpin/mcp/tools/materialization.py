"""Bounded, read-only inspection of canonical product coverage."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from lynchpin.core.config import get_config
from lynchpin.materialization import (
    _audit_one,
    audit_materialization,
    materialized_dataset_coverage,
)
from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES


def materialization_report(
    *,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    detail: bool = False,
) -> list[dict[str, Any]]:
    if source is not None and source not in SOURCE_CONTRACT_NAMES:
        raise ValueError(f"unknown source: {source}")
    if detail and source is None:
        raise ValueError("detail requires a source")
    if (start is None) != (end is None):
        raise ValueError("start and end must be supplied together")
    first = date.fromisoformat(start) if start is not None else None
    last = date.fromisoformat(end) if end is not None else None
    if first is not None and last is not None and last < first:
        raise ValueError("end must not precede start")
    until = last + timedelta(days=1) if last is not None else None
    rows = [_audit_one(source, cfg=get_config())] if source else audit_materialization()
    result: list[dict[str, Any]] = []
    for row in rows:
        if detail:
            payload = row.to_json()
        else:
            payload = {
                "name": row.name,
                "status": row.status,
                "tail_stale": row.tail_stale,
                "repair_required": row.repair_required,
                "reason": row.reason,
                "row_count": row.row_count,
                "first_date": row.first_date.isoformat() if row.first_date else None,
                "last_date": row.last_date.isoformat() if row.last_date else None,
                "covered_date_count": len(row.covered_dates),
                "materialized_path_count": len(row.materialized_paths),
            }
        if first is not None:
            payload["requested_window"] = {"start": start, "end": end, "end_inclusive": True}
            payload["coverage"] = materialized_dataset_coverage(row, start=first, end=until)
        result.append(payload)
    return result
