"""Materialize the dated ambient-intelligence product for Sinnix."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.config import get_config
from ..core.io import resolve_analysis_path, save_json

SCHEMA = "lynchpin-ambient-intelligence-v1"
FRAGMENTATION_NUDGE_THRESHOLD = 0.65
NUDGE_COOLDOWN_SECONDS = 60 * 60


def build_ambient_intelligence(
    *,
    logical_day: date,
    circadian: Sequence[Mapping[str, Any]] = (),
    fragmentation: Sequence[Mapping[str, Any]] = (),
    anomalies: Sequence[Mapping[str, Any]] = (),
    chores: Sequence[Mapping[str, Any]] = (),
    generated_at: datetime | None = None,
    nudge_opt_in: bool = False,
    dnd: bool = False,
    last_nudge_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    day = logical_day.isoformat()
    day_circadian = [row for row in circadian if str(row.get("date")) == day]
    day_fragmentation = [row for row in fragmentation if str(row.get("date")) == day]
    latest_fragmentation = day_fragmentation[-1] if day_fragmentation else None
    risk = float((latest_fragmentation or {}).get("fragmentation", 0.0) or 0.0)
    cooldown = last_nudge_at is not None and (generated_at - last_nudge_at).total_seconds() < NUDGE_COOLDOWN_SECONDS
    nudge_reason = "disabled"
    if nudge_opt_in:
        nudge_reason = "dnd" if dnd else "rate_limited" if cooldown else "below_threshold" if risk < FRAGMENTATION_NUDGE_THRESHOLD else "eligible"
    evidence = {
        "circadian": "lynchpin.activitywatch_derived:circadian",
        "fragmentation": "lynchpin.activitywatch_derived:fragmentation",
        "anomalies": "lynchpin.temporal_signals",
        "chores": "sinnix-beads-and-runtime-ledgers",
    }
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "logical_date": day,
        "confidence": "observed" if day_circadian or day_fragmentation else "insufficient_data",
        "circadian": {"rows": list(day_circadian), "position_hour": datetime.now().hour},
        "fragmentation": {
            "risk": round(risk, 6),
            "rows": list(day_fragmentation),
            "nudge": {"opt_in": nudge_opt_in, "dnd": dnd, "eligible": nudge_reason == "eligible", "reason": nudge_reason},
        },
        "anomalies": [{"item": dict(row), "evidence": evidence["anomalies"]} for row in anomalies if isinstance(row, Mapping)],
        "chores": [{"item": dict(row), "evidence": row.get("evidence", evidence["chores"])} for row in chores if isinstance(row, Mapping)],
        "evidence": evidence,
    }


def write_ambient_intelligence(out_file: str | Path | None = None, *, logical_day: date | None = None) -> dict[str, Any]:
    """Read canonical products and publish one bounded JSON snapshot."""
    day = logical_day or date.today()
    base = get_config().derived_root
    circadian = _read_ndjson(base / "activitywatch/graph/circadian.ndjson")
    fragmentation = _read_ndjson(base / "activitywatch/graph/fragmentation.ndjson")
    signals = _read_ndjson(base / "temporal/signals.ndjson")
    anomalies = [row for row in signals if str(row.get("kind")) == "temporal_anomaly" and str(row.get("event_date")) == day.isoformat()]
    chores = _load_chores()
    payload = build_ambient_intelligence(
        logical_day=day,
        circadian=circadian,
        fragmentation=fragmentation,
        anomalies=anomalies,
        chores=chores,
        nudge_opt_in=os.environ.get("SINNIX_FRAGMENTATION_NUDGE", "0") == "1",
        dnd=os.environ.get("SINNIX_DND", "0") == "1",
    )
    target = Path(out_file) if out_file else Path(resolve_analysis_path("ambient_intelligence.json"))
    save_json(target, payload, sort_keys=True)
    return payload


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _load_chores() -> list[dict[str, Any]]:
    path = get_config().sinnix_root / ".beads/issues.jsonl"
    rows = _read_ndjson(path)
    return [
        {"id": row["id"], "title": row.get("title", ""), "priority": row.get("priority"), "status": row.get("status"), "evidence": f"bead:{row['id']}"}
        for row in rows
        if row.get("status") in {"open", "in_progress"} and row.get("id")
    ][:8]


__all__ = ["SCHEMA", "build_ambient_intelligence", "write_ambient_intelligence"]
