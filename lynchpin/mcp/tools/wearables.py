"""Read phone captures, vendor revisions, and materialized health coverage."""

from datetime import date
from itertools import islice
import json
from typing import Any


def wearable_records(
    view: str,
    start: str | None = None,
    end: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    from lynchpin.ingest.health_coverage_materialize import health_coverage_path
    from lynchpin.sources.phone_events import phone_events
    from lynchpin.sources.xiaomi_cloud import latest_envelopes

    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    first = date.fromisoformat(start) if start else None
    last = date.fromisoformat(end) if end else None
    if first and last and last < first:
        raise ValueError("end must not precede start")
    caveats: list[str] = []
    artifact = None
    modified_at = None
    if view in {"phone", "phone_health"}:
        rows = (
            event.payload
            for event in phone_events(start=first, end=last)
            if (view == "phone" or event.kind.startswith("health_"))
            and (source is None or event.payload.get("source") == source)
        )
        basis = "capture timestamp, inclusive local dates"
        caveats.append("Health measurement start/end may precede capture time during backfill; records may contain multiple samples.")
    elif view == "xiaomi":
        envelopes = latest_envelopes()
        rows = (
            envelope.payload
            for (_, day), envelope in sorted(envelopes.items(), key=lambda item: (str(item[0][1]), item[0][0]))
            if day is not None and (first is None or day >= first) and (last is None or day <= last)
        )
        basis = "vendor measurement day, inclusive; latest captured revision per kind/day"
        caveats.append("Cloud capture time is not measurement freshness; failed fetches do not erase previous revisions.")
    elif view == "coverage":
        artifact = health_coverage_path()
        if not artifact.is_file():
            return {"status": "unavailable", "reason": "health coverage is not materialized", "artifact": str(artifact), "rows": []}
        from datetime import datetime, timezone
        modified_at = datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc).isoformat()
        # Global coverage groups cannot be clipped into a narrower date window.
        if first is not None or last is not None:
            raise ValueError("coverage is a whole-history product; omit start and end")
        with artifact.open() as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        rows = iter(records)
        basis = "whole-history materialized coverage"
        caveats.append("This report reflects its artifact timestamp; later phone/cloud arrivals require a coverage refresh.")
    else:
        raise ValueError(f"unknown wearable view: {view}")
    selected = list(islice(rows, limit + 1))
    return {
        "status": "ok" if selected else "empty",
        "view": view,
        "date_basis": basis,
        "artifact": str(artifact) if artifact else None,
        "artifact_modified_at": modified_at,
        "rows": selected[:limit],
        "truncated": len(selected) > limit,
        "limit": limit,
        "caveats": caveats,
    }
