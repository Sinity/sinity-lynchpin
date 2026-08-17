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
when this report matters most -- joined against the Xiaomi cloud witness
lane (``lynchpin.sources.xiaomi_cloud``) for the two-independent-witness
check the vendor service exists for (sinnix-ogll): per night, the vendor's
sleep segments against Health Connect's sleep sessions with computed overlap
minutes; per day, the vendor's dense heart-rate sample count against HC's
unique record count. The two paths share no code and no transport (band ->
Mi Fitness -> Xiaomi cloud vs band -> Mi Fitness -> HC -> phone app), so
agreement is real corroboration and divergence localizes which leg dropped
data. Output is one heterogeneous NDJSON product (``row`` field
discriminates: ``group`` / ``gap`` / ``sweep`` / ``lane`` / ``anomaly`` /
``witness`` / ``summary``) plus the usual manifest.

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
from ..sources import xiaomi_cloud
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
_RECEIPT_KINDS = {
    "health_backfill",
    "health_lane_state",
    "health_sweep_failed",
    "health_deletion",
    "lane_blocked",
}

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


def _epoch_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, (int, float)) or not value:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _overlap_seconds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    return max(0.0, (min(a_end, b_end) - max(a_start, b_start)).total_seconds())


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Union of possibly-overlapping intervals. Legacy HC sleep sessions are
    re-emitted with drifting bounds, so the same night appears several times;
    summing them double-counts, the union does not."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# Xiaomi's raw sleep objects label each minute-level segment with a numeric
# state. Decoded 2026-08-17 against the same night's aggregate, whose
# sleep_deep/light/rem/awake minutes the per-state sums reproduce.
_VENDOR_SLEEP_STATES = {2: "deep", 3: "light", 4: "rem", 5: "awake"}

# The phone importer generation that first paged Health Connect to exhaustion.
# Sweeps recorded by earlier generations stopped at the provider's page cap, so
# the history behind them is a floor.
_PAGINATED_GENERATION = 3


def _vendor_stage_minutes(records: Any) -> dict[str, float]:
    """Minutes per sleep stage from the vendor's raw stage transitions.

    Paired in the witness row with the stage labels Health Connect offered for
    the same day, because the two disagree in a specific way: Mi Fitness
    stages the main night into HC but writes short daytime sessions as one
    ``unknown`` block, while this lane stages every segment. An unrecognized
    state code is reported under its own number rather than silently folded
    into a known stage.
    """
    minutes: dict[str, float] = defaultdict(float)
    for record in records if isinstance(records, list) else []:
        value = record.get("value") if isinstance(record, dict) else None
        for item in (value or {}).get("items") or [] if isinstance(value, dict) else []:
            if not isinstance(item, dict):
                continue
            start, end = item.get("start_time"), item.get("end_time")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            if end <= start:
                continue
            state = item.get("state")
            label = _VENDOR_SLEEP_STATES.get(state, f"state_{state}")
            minutes[label] += (end - start) / 60
    return {k: round(v, 1) for k, v in sorted(minutes.items())}


def _witness_rows(
    hc_sleep_sessions: dict[str, tuple[datetime, Optional[datetime]]],
    hc_hr_days: dict[str, set[str]],
    hc_sleep_stage_labels: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Vendor-vs-HC cross-check rows, restricted to days the vendor lane holds.

    The vendor lane is young (rolling-window witness, live 2026-08-17); the
    HC side spans years. Emitting a witness row for every historical HC day
    would drown the comparison, so the vendor lane picks the days and the
    group/gap rows keep answering for deep history. Heart-rate numbers are
    deliberately NOT ratioed: vendor samples are individual bpm readings
    while HC HeartRateRecords are series records each carrying many samples,
    so the two counts corroborate presence and density, not equality.
    """
    latest = xiaomi_cloud.latest_envelopes(
        kinds={"vendor_sleep", "vendor_raw_heart_rate", "vendor_raw_sleep"}
    )
    rows: list[dict[str, Any]] = []
    hr_vendor: dict[str, int] = {}
    stages_vendor: dict[str, dict[str, float]] = {}
    for (kind, day), envelope in sorted(
        latest.items(), key=lambda item: (item[0][0], item[0][1] or datetime.min.date())
    ):
        if day is None or not isinstance(envelope.data, dict):
            continue
        if kind == "vendor_raw_heart_rate":
            count = envelope.data.get("count")
            hr_vendor[day.isoformat()] = (
                count if isinstance(count, int) else len(envelope.data.get("records") or [])
            )
            continue
        if kind == "vendor_raw_sleep":
            stages_vendor[day.isoformat()] = _vendor_stage_minutes(envelope.data.get("records"))
            continue
        segments = [
            (bed, wake)
            for segment in envelope.data.get("segment_details") or []
            if isinstance(segment, dict)
            for bed in (_epoch_utc(segment.get("bedtime")),)
            for wake in (_epoch_utc(segment.get("wake_up_time")),)
            if bed is not None and wake is not None and wake > bed
        ]
        matched: list[tuple[datetime, datetime]] = []
        for _rid, (start, end) in hc_sleep_sessions.items():
            if end is None:
                continue
            if any(_overlap_seconds(start, end, bed, wake) > 0 for bed, wake in segments):
                matched.append((start, end))
        hc_union = _merge_intervals(matched)
        segment_union = _merge_intervals(segments)
        hc_minutes = sum((end - start).total_seconds() for start, end in hc_union) / 60
        overlap_minutes = (
            sum(
                _overlap_seconds(h_start, h_end, s_start, s_end)
                for h_start, h_end in hc_union
                for s_start, s_end in segment_union
            )
            / 60
        )
        rows.append(
            {
                "row": "witness",
                "metric": "sleep",
                "day": day.isoformat(),
                "vendor_segments": len(segments),
                "vendor_sleep_minutes": envelope.data.get("total_duration"),
                "vendor_sleep_score": envelope.data.get("sleep_score"),
                "vendor_bedtime": _iso(min((bed for bed, _ in segments), default=None)),
                "vendor_wake": _iso(max((wake for _, wake in segments), default=None)),
                "hc_sessions": len(matched),
                "hc_sleep_minutes": round(hc_minutes, 1),
                "overlap_minutes": round(overlap_minutes, 1),
                "both_witnesses": bool(segments) and bool(matched),
                # Architecture, and how much of it each witness kept. A day
                # whose hc_stage_labels is only {"unknown"} is a day whose
                # sleep structure survives in the vendor lane alone -- which
                # is what naps look like, and what a firmware or app change
                # would look like if it ever stopped staging the night too.
                "vendor_stage_minutes": stages_vendor.get(day.isoformat()) or None,
                "hc_stage_labels": sorted(hc_sleep_stage_labels.get(day.isoformat(), ())) or None,
            }
        )
    for day in sorted(hr_vendor):
        rows.append(
            {
                "row": "witness",
                "metric": "heart_rate",
                "day": day,
                "vendor_samples": hr_vendor[day],
                "hc_records": len(hc_hr_days.get(day, ())),
                "both_witnesses": hr_vendor[day] > 0 and bool(hc_hr_days.get(day)),
            }
        )
    return rows


def materialize_health_coverage(*, output: Optional[Path] = None) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], _Group] = defaultdict(_Group)
    # Unique-record measurement times per (kind, source), for gap analysis.
    # Keyed per record_id so a record emitted 473 times contributes once.
    gap_times: dict[tuple[str, str], dict[str, datetime]] = defaultdict(dict)
    sweeps: dict[str, dict[str, Any]] = {}
    # Last-wins: the newest state record is the current answer.
    lane_state: dict[str, Any] = {}
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
    # Two-witness inputs: HC sleep sessions (rid -> start/end) and unique HC
    # heart-rate record ids per UTC day, joined against the vendor lane below.
    hc_sleep_sessions: dict[str, tuple[datetime, Optional[datetime]]] = {}
    hc_hr_days: dict[str, set[str]] = defaultdict(set)
    hc_sleep_stage_labels: dict[str, set[str]] = defaultdict(set)

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
            elif kind == "health_lane_state":
                # The lane's own answer to "which types are swept", restated
                # every tick. It supersedes the receipts for that question:
                # a receipt proves a sweep finished at a moment, and only if
                # that moment's line survived into the lake. Seventeen types
                # are swept on this device with no retained receipt for any of
                # them, so without this the report could only say "unknown"
                # about the bulk of the history it exists to describe.
                lane_state = {
                    "at": payload.get("ts"),
                    "generation": payload.get("generation"),
                    "swept": [s for s in payload.get("swept") or [] if isinstance(s, str)],
                    "unswept": [s for s in payload.get("unswept") or [] if isinstance(s, str)],
                    "ungranted": [s for s in payload.get("ungranted") or [] if isinstance(s, str)],
                    "rate_limited": payload.get("rate_limited"),
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
            # Witness collectors accept rid-less records: every pre-metadata-
            # rewrite event lacks record_id (all 259 historical sleep sessions
            # do), and requiring one would report false vendor/HC divergence.
            # The legacy key dedups re-emissions by measurement bounds instead.
            if kind == "health_sleep":
                skey = (
                    rid
                    if isinstance(rid, str) and rid
                    else f"legacy:{_iso(measured)}|{payload.get('end') or ''}"
                )
                hc_sleep_sessions.setdefault(skey, (measured, _parse_ts(payload.get("end"))))
                day_key = measured.astimezone(timezone.utc).date().isoformat()
                for stage in payload.get("stages") or []:
                    if isinstance(stage, dict) and isinstance(stage.get("stage"), str):
                        hc_sleep_stage_labels[day_key].add(stage["stage"])
            elif kind == "health_heart_rate":
                hkey = rid if isinstance(rid, str) and rid else f"legacy:{_iso(measured)}"
                hc_hr_days[measured.astimezone(timezone.utc).date().isoformat()].add(hkey)

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
        set(sweeps)
        | set(sweep_failures)
        | set(deletions)
        | set(KIND_TO_RECORD_TYPE.values())
        | set(lane_state.get("swept") or ())
        | set(lane_state.get("unswept") or ())
        | set(lane_state.get("ungranted") or ())
    )
    state_swept = set(lane_state.get("swept") or ())
    state_known = state_swept | set(lane_state.get("unswept") or ()) | set(
        lane_state.get("ungranted") or ()
    )
    for rtype in all_types:
        completed = sweeps.get(rtype)
        if (
            completed is None
            and rtype not in sweep_failures
            and rtype not in deletions
            and rtype not in state_known
        ):
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
                # A type whose newest sweep predates the paginated importer
                # was read by one that stopped at the provider's first page
                # cap, so its span is a floor rather than a history. The row
                # says so instead of leaving every reader to remember which
                # generation could be trusted.
                "span_trustworthy": (completed or {}).get("generation", 0) >= _PAGINATED_GENERATION,
                # What the lane says about itself right now, which is a
                # different claim from "a completion receipt survived in the
                # lake". Where they disagree the state wins for swept-ness and
                # the receipt still supplies the counts.
                "state_swept": rtype in state_swept if state_known else None,
                "state_granted": (
                    rtype not in set(lane_state.get("ungranted") or ()) if state_known else None
                ),
            }
        )

    for reason, info in sorted(lane_blocks.items()):
        rows.append({"row": "lane", "reason": reason, **info})

    rows.extend(anomalies.values())

    witness_rows = _witness_rows(hc_sleep_sessions, hc_hr_days, hc_sleep_stage_labels)
    rows.extend(witness_rows)

    rows.append(
        {
            "row": "summary",
            "health_events": health_events,
            "groups": len(groups),
            "unique_records_total": sum(len(g.record_ids) for g in groups.values()),
            "routes_pending_consent": len(routes_pending - routes_with_data),
            "anomalies": len(anomalies),
            "witness_days": len(witness_rows),
            "witness_corroborated": sum(1 for r in witness_rows if r.get("both_witnesses")),
            # Straight from the lane's newest state record; null when the
            # phone has not yet reported one (a build older than 2026-08-17).
            "lane_state_at": lane_state.get("at"),
            "types_swept": len(state_swept) if state_known else None,
            "types_unswept": len(lane_state.get("unswept") or ()) if state_known else None,
            "types_ungranted": len(lane_state.get("ungranted") or ()) if state_known else None,
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
