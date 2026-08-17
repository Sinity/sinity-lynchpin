"""Source/device-qualified health coverage report over the phone events plane.

The misreading this product exists to prevent (sinnix-3jnc): bare totals over
``health_*`` events answered "how much heart-rate coverage do we have?" with
"five months" when the truth was two disconnected blocks -- and, later, with
"a quarter-million steps records" when the truth was one ~162-record page
re-read 1,592 times by a pagination loop. Both failures share a shape: event
counts and min/max timestamps stand in for coverage, and neither survives
duplication or gaps.

So this materializer reports coverage the way it must be counted:

* canonical unique records (by Health Connect ``record_id``), never event
  totals -- events per record is itself reported, because a high ratio is a
  capture-defect signal, not volume;
* grouped by record kind x source package x device model x recording method,
  so "Samsung sleep: 251 / Band 10 sleep: N" is the native answer shape;
* with the largest internal gap and a gap histogram per (kind, source),
  computed over unique record start times -- the only honest way to see a
  hole between two dense blocks;
* with the sweep's own receipts joined in (``health_backfill`` completions,
  ``health_sweep_failed`` pauses, ``lane_blocked`` reasons, typed
  ``health_deletion`` tombstones, pending ``consent_required`` routes), so
  "the importer finished" is a recorded fact per type rather than an
  inference from data volume;
* with timestamp anomalies surfaced instead of aggregated over: one Samsung
  record carries ``start=1970-01-01T00:00:00Z``, and a ``min()`` that
  swallows it reports 56 years of coverage.

Input is the phone events plane (``lynchpin.sources.phone_events``), streamed
-- day files reach gigabytes when an importer misbehaves, which is exactly
when this report matters most. Output is one heterogeneous NDJSON product
(``row`` field discriminates: ``group`` / ``gap`` / ``sweep`` / ``lane`` /
``anomaly`` / ``summary``) plus the usual manifest.

Rebuilds are always whole-history: coverage is a global property, and a
windowed rebuild of a gap report would reintroduce the exact blindness the
product exists to remove.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.config import get_config
from ..sources.phone_events import phone_events
from ._manifest import atomic_write_ndjson, write_manifest

__all__ = [
    "health_coverage_path",
    "materialize_health_coverage",
    "main",
]

#: Kotlin emitter kind -> Health Connect record-type simple name, mirroring
#: ``HealthLane.emit()`` in the sinnix phone app. Joins sweep receipts
#: (which carry the type name) onto data groups (which carry the kind).
KIND_TO_RECORD_TYPE: dict[str, str] = {
    "health_steps": "StepsRecord",
    "health_heart_rate": "HeartRateRecord",
    "health_sleep": "SleepSessionRecord",
    "health_spo2": "OxygenSaturationRecord",
    "health_hrv": "HeartRateVariabilityRmssdRecord",
    "health_respiratory_rate": "RespiratoryRateRecord",
    "health_resting_heart_rate": "RestingHeartRateRecord",
    "health_active_calories": "ActiveCaloriesBurnedRecord",
    "health_total_calories": "TotalCaloriesBurnedRecord",
    "health_distance": "DistanceRecord",
    "health_speed": "SpeedRecord",
    "health_elevation": "ElevationGainedRecord",
    "health_exercise": "ExerciseSessionRecord",
    "health_vo2max": "Vo2MaxRecord",
    "health_body_temperature": "BodyTemperatureRecord",
    "health_blood_pressure": "BloodPressureRecord",
    "health_weight": "WeightRecord",
    "health_floors_climbed": "FloorsClimbedRecord",
    "health_skin_temperature": "SkinTemperatureRecord",
    "health_blood_glucose": "BloodGlucoseRecord",
    "health_body_fat": "BodyFatRecord",
    "health_basal_metabolic_rate": "BasalMetabolicRateRecord",
    "health_height": "HeightRecord",
    "health_hydration": "HydrationRecord",
}

#: Lifecycle kinds consumed for receipts rather than counted as data.
_RECEIPT_KINDS = {"health_backfill", "health_sweep_failed", "health_deletion", "lane_blocked"}

#: Measurement timestamps before this are treated as corrupt provider data
#: (observed: one Samsung HR record stamped 1970-01-01) -- surfaced as
#: anomaly rows, excluded from coverage bounds and gap analysis.
_ANOMALY_FLOOR = datetime(2000, 1, 1, tzinfo=timezone.utc)

#: Gap histogram bucket upper bounds, seconds (label, bound).
_GAP_BUCKETS: tuple[tuple[str, float], ...] = (
    ("<=2m", 120.0),
    ("<=10m", 600.0),
    ("<=1h", 3_600.0),
    ("<=6h", 21_600.0),
    ("<=24h", 86_400.0),
    ("<=7d", 604_800.0),
    (">7d", float("inf")),
)


def health_coverage_path(root: Optional[Path] = None) -> Path:
    base = root or get_config().derived_root
    return base / "health/health_coverage.ndjson"


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _measurement_time(payload: dict[str, Any]) -> Optional[datetime]:
    # Interval records carry start/end; instantaneous ones carry time.
    return _parse_ts(payload.get("start")) or _parse_ts(payload.get("time"))


@dataclass
class _Group:
    events: int = 0
    with_id: int = 0
    record_ids: set[str] = field(default_factory=set)
    earliest: Optional[datetime] = None
    latest: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    last_emitted: Optional[datetime] = None


def _bucket_label(seconds: float) -> str:
    for label, bound in _GAP_BUCKETS:
        if seconds <= bound:
            return label
    return _GAP_BUCKETS[-1][0]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def materialize_health_coverage(*, output: Optional[Path] = None) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], _Group] = defaultdict(_Group)
    # Unique-record measurement times per (kind, source), for gap analysis.
    # Keyed per record_id so a record emitted 473 times contributes once.
    gap_times: dict[tuple[str, str], dict[str, datetime]] = defaultdict(dict)
    sweeps: dict[str, dict[str, Any]] = {}
    sweep_failures: Counter[str] = Counter()
    last_sweep_failure: dict[str, dict[str, Any]] = {}
    deletions: dict[str, set[str]] = defaultdict(set)
    lane_blocks: dict[str, dict[str, Any]] = {}
    # Keyed so a record emitted hundreds of times (the flood shape) yields
    # one anomaly row with an emission count, not hundreds of rows.
    anomalies: dict[tuple[str, str, Any, Optional[str]], dict[str, Any]] = {}
    routes_pending: set[str] = set()
    routes_with_data: set[str] = set()
    health_events = 0

    for event in phone_events():
        kind = event.kind
        if not (kind.startswith("health_") or kind == "lane_blocked"):
            continue
        payload = event.payload
        health_events += 1

        if kind in _RECEIPT_KINDS:
            if kind == "health_backfill":
                # Last receipt per type wins; counters can be flood-inflated
                # (they resume across duplicate passes), so the receipt is
                # evidence of COMPLETION, never of volume.
                rtype = str(payload.get("type"))
                sweeps[rtype] = {
                    "records_counter": payload.get("records"),
                    "pages": payload.get("pages"),
                    "generation": payload.get("generation"),
                    "completed_at": payload.get("ts"),
                }
            elif kind == "health_sweep_failed":
                rtype = str(payload.get("type"))
                sweep_failures[rtype] += 1
                last_sweep_failure[rtype] = {
                    "reason": payload.get("reason"),
                    "pages_read": payload.get("pages_read"),
                    "at": payload.get("ts"),
                }
            elif kind == "health_deletion":
                rid = payload.get("record_id")
                if isinstance(rid, str):
                    deletions[str(payload.get("type"))].add(rid)
            elif payload.get("lane") == "health":
                reason = str(payload.get("reason", ""))
                lane_blocks[reason] = {"last_at": payload.get("ts")}
            continue

        source = str(payload.get("source") or "unknown")
        device = str(payload.get("device_model") or "unknown")
        method = str(payload.get("recording_method") or "unknown")
        group = groups[(kind, source, device, method)]
        group.events += 1

        emitted = _parse_ts(payload.get("ts"))
        if emitted and (group.last_emitted is None or emitted > group.last_emitted):
            group.last_emitted = emitted
        modified = _parse_ts(payload.get("modified"))
        if modified and (group.last_modified is None or modified > group.last_modified):
            group.last_modified = modified

        rid = payload.get("record_id")
        if isinstance(rid, str) and rid:
            group.with_id += 1
            group.record_ids.add(rid)

        measured = _measurement_time(payload)
        if measured is None:
            pass
        elif measured < _ANOMALY_FLOOR:
            key = (kind, source, rid, _iso(measured))
            row = anomalies.setdefault(
                key,
                {
                    "row": "anomaly",
                    "kind": kind,
                    "source": source,
                    "record_id": rid,
                    "measured": _iso(measured),
                    "events": 0,
                    "reason": "measurement time before 2000-01-01; excluded from bounds and gaps",
                },
            )
            row["events"] += 1
        else:
            if group.earliest is None or measured < group.earliest:
                group.earliest = measured
            if group.latest is None or measured > group.latest:
                group.latest = measured
            if isinstance(rid, str) and rid:
                gap_times[(kind, source)].setdefault(rid, measured)

        if kind == "health_exercise" and isinstance(rid, str):
            route = payload.get("route")
            if route == "consent_required":
                routes_pending.add(rid)
            elif route == "data":
                routes_with_data.add(rid)

    rows: list[dict[str, Any]] = []
    for (kind, source, device, method), group in sorted(groups.items()):
        unique = len(group.record_ids)
        rows.append(
            {
                "row": "group",
                "kind": kind,
                "record_type": KIND_TO_RECORD_TYPE.get(kind),
                "source": source,
                "device_model": device,
                "recording_method": method,
                "unique_records": unique,
                "events": group.events,
                "events_without_record_id": group.events - group.with_id,
                # >1 means re-emission (deliberate re-sweeps or a defect);
                # the flood signature is this ratio in the hundreds.
                "events_per_record": round(group.with_id / unique, 2) if unique else None,
                "earliest": _iso(group.earliest),
                "latest": _iso(group.latest),
                "last_provider_write": _iso(group.last_modified),
                "last_emitted": _iso(group.last_emitted),
            }
        )

    for (kind, source), times in sorted(gap_times.items()):
        ordered = sorted(times.values())
        histogram: Counter[str] = Counter()
        largest: Optional[float] = None
        largest_from: Optional[datetime] = None
        for previous, current in zip(ordered, ordered[1:]):
            seconds = (current - previous).total_seconds()
            histogram[_bucket_label(seconds)] += 1
            if largest is None or seconds > largest:
                largest = seconds
                largest_from = previous
        rows.append(
            {
                "row": "gap",
                "kind": kind,
                "source": source,
                "unique_records": len(ordered),
                "largest_gap_seconds": largest,
                "largest_gap_from": _iso(largest_from),
                "gap_histogram": {label: histogram.get(label, 0) for label, _ in _GAP_BUCKETS},
            }
        )

    all_types = sorted(
        set(sweeps) | set(sweep_failures) | set(deletions) | set(KIND_TO_RECORD_TYPE.values())
    )
    for rtype in all_types:
        completed = sweeps.get(rtype)
        if completed is None and rtype not in sweep_failures and rtype not in deletions:
            continue
        rows.append(
            {
                "row": "sweep",
                "record_type": rtype,
                "sweep_completed": completed is not None,
                **(completed or {}),
                "sweep_failures": sweep_failures.get(rtype, 0),
                "last_failure": last_sweep_failure.get(rtype),
                "deleted_records": len(deletions.get(rtype, ())),
            }
        )

    for reason, info in sorted(lane_blocks.items()):
        rows.append({"row": "lane", "reason": reason, **info})

    rows.extend(anomalies.values())

    rows.append(
        {
            "row": "summary",
            "health_events": health_events,
            "groups": len(groups),
            "unique_records_total": sum(len(g.record_ids) for g in groups.values()),
            "routes_pending_consent": len(routes_pending - routes_with_data),
            "anomalies": len(anomalies),
        }
    )

    path = output or health_coverage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_ndjson(path, rows)
    write_manifest(
        path.with_suffix(".manifest.json"),
        {
            "dataset": "health_coverage",
            "row_count": len(rows),
            "health_events_scanned": health_events,
            "unique_records_total": sum(len(g.record_ids) for g in groups.values()),
        },
    )
    return {"row_count": len(rows), "output": str(path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the health coverage report")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_health_coverage(output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
