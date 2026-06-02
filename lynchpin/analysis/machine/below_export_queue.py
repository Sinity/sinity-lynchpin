"""Durable queue of live ``below`` windows to export for pressure attribution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.attribution import BelowPressureWindowPlan, plan_below_windows_for_pressure_episodes
from lynchpin.analysis.machine.below import DEFAULT_LIVE_BELOW_STORE, DEFAULT_STABILITY_ROOT, failed_below_exports
from lynchpin.core.io import save_json


@dataclass(frozen=True)
class BelowExportQueue:
    queue_count: int
    failed_capture_count: int
    root: str
    live_store: str
    generated_for: dict[str, Any]
    items: list[BelowPressureWindowPlan]
    failed_captures: list[dict[str, Any]]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_below_export_queue(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    limit: int = 25,
    padding_seconds: int = 60,
    min_duration_seconds: int = 120,
) -> BelowExportQueue:
    failed = failed_below_exports(root=root)
    plans = plan_below_windows_for_pressure_episodes(
        start=start,
        end=end,
        path=path,
        root=root,
        live_store=live_store,
        limit=limit,
        padding_seconds=padding_seconds,
        min_duration_seconds=min_duration_seconds,
    )
    caveats = [
        "queue is dry-run planning only; use machine-below-export-pressure-windows --write to materialize bounded CSV windows",
    ]
    if failed:
        caveats.append("failed/header-only bounded below exports are skipped by default; delete or repair failed report directories to retry")
    if not plans:
        caveats.append("no residual pressure windows currently need live below export")
    return BelowExportQueue(
        queue_count=len(plans),
        failed_capture_count=len(failed),
        root=str(root),
        live_store=str(live_store),
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "limit": limit,
            "padding_seconds": padding_seconds,
            "min_duration_seconds": min_duration_seconds,
        },
        items=plans,
        failed_captures=[
            {
                "capture_id": row.capture_id,
                "report_path": row.report_path,
                "missing_files": row.missing_files,
                "empty_files": row.empty_files,
            }
            for row in failed
        ],
        caveats=caveats,
    )


def write_below_export_queue(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    limit: int = 25,
    padding_seconds: int = 60,
    min_duration_seconds: int = 120,
) -> BelowExportQueue:
    queue = analyze_below_export_queue(
        start=start,
        end=end,
        path=path,
        root=root,
        live_store=live_store,
        limit=limit,
        padding_seconds=padding_seconds,
        min_duration_seconds=min_duration_seconds,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **queue.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return queue


__all__ = ["BelowExportQueue", "analyze_below_export_queue", "write_below_export_queue"]
