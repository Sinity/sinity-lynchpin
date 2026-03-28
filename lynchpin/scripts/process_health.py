#!/usr/bin/env python3
"""Process Samsung Health exports into unified health JSONL.

Reads all Samsung Health export directories, deduplicates by datauuid
(latest export wins for each record), and writes:
  - health_sleep.jsonl     — all sleep records (naps + full sleep)
  - health_stress.jsonl    — stress measurements
  - health_steps.jsonl     — daily step counts
  - health_hrv.jsonl       — heart rate variability
  - health_vitality.jsonl  — daily vitality scores

Usage:
    python -m lynchpin.scripts.process_health [--dry-run]
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEALTH_RAW = Path("/realm/data/exports/health/raw/samsung-health")
SAA_RAW = Path("/realm/data/exports/health/raw/sleep-as-android")
PROCESSED = Path("/realm/data/exports/health/processed")


# ── Parsing helpers ──────────────────────────────────────────────────────────

def read_samsung_csv(path: Path) -> list[dict]:
    """Samsung Health CSV: line 1 = metadata, line 2 = header, rest = data."""
    with open(path, encoding='utf-8-sig') as f:
        f.readline()  # skip metadata
        reader = csv.DictReader(f)
        return [row for row in reader if any(row.values())]


def parse_offset(offset_str: str) -> float:
    """Parse 'UTC+0200' → 2.0 hours."""
    if not offset_str or not offset_str.startswith("UTC"):
        return 0.0
    sign = 1 if '+' in offset_str else -1
    num = offset_str.replace("UTC", "").replace("+", "").replace("-", "")
    if len(num) == 4:
        return sign * (int(num[:2]) + int(num[2:]) / 60)
    return sign * float(num) if num else 0.0


def parse_dt(value: str | None, offset_str: str = "UTC+0000") -> str | None:
    """Parse Samsung datetime → ISO 8601 with timezone."""
    if not value or value.startswith("1970"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    hours = parse_offset(offset_str)
    tz = timezone(timedelta(hours=hours))
    return naive.replace(tzinfo=tz).isoformat()


def try_float(v: str | None) -> float | None:
    if v is None or v == '' or str(v).lower() == 'nan':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def try_int(v: str | None) -> int | None:
    if v is None or v == '' or str(v).lower() == 'nan':
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# ── Sleep processing ─────────────────────────────────────────────────────────

def process_sleep(dry_run: bool = False) -> int:
    """Process all Samsung Health sleep exports + SAA into unified JSONL."""
    # 1. Load existing sleep_all_nights.jsonl to preserve SAA fusion data
    all_nights_path = PROCESSED / "sleep_all_nights.jsonl"
    existing: dict[str, dict] = {}
    if all_nights_path.exists():
        with open(all_nights_path) as f:
            for line in f:
                rec = json.loads(line)
                cid = rec.get("canonical_id", "")
                if cid:
                    existing[cid] = rec

    # 2. Process all Samsung Health sleep_combined exports
    new_records: dict[str, dict] = {}
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.sleep_combined.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            datauuid = row.get('datauuid', '')
            if not datauuid:
                continue
            offset_str = row.get('time_offset', 'UTC+0000')
            start = parse_dt(row.get('start_time'), offset_str)
            end = parse_dt(row.get('end_time'), offset_str)
            if not start or not end:
                continue

            new_records[datauuid] = {
                "canonical_id": datauuid,
                "source": "samsung_only",
                "start_local": start,
                "end_local": end,
                "duration_minutes": try_float(row.get('sleep_duration')) or 0,
                "device_uuid": row.get('deviceuuid', ''),
                "device_name": "",
                "time_offset_hours": parse_offset(offset_str),
                "sleep_metrics": {
                    "sleep_score": try_float(row.get('sleep_score')),
                    "sleep_duration": try_float(row.get('sleep_duration')),
                    "total_rem_duration": try_float(row.get('total_rem_duration')),
                    "total_light_duration": try_float(row.get('total_light_duration')),
                    "deep_score": try_float(row.get('deep_score')),
                    "rem_score": try_float(row.get('rem_score')),
                    "wake_score": try_float(row.get('wake_score')),
                    "physical_recovery": try_float(row.get('physical_recovery')),
                    "mental_recovery": try_float(row.get('mental_recovery')),
                    "sleep_efficiency": try_float(row.get('efficiency')),
                    "sleep_cycle": try_float(row.get('sleep_cycle')),
                    "movement_awakening": try_float(row.get('movement_awakening')),
                },
                "saa_metrics": None,
                "deltas": None,
                "comment": None,
            }

    # 3. Merge: existing (with SAA fusion) take priority
    merged = dict(existing)
    added = 0
    for uuid, rec in new_records.items():
        if uuid not in merged:
            merged[uuid] = rec
            added += 1
        else:
            # Enrich existing metrics from newer export
            old_m = merged[uuid].get('sleep_metrics') or {}
            new_m = rec.get('sleep_metrics') or {}
            for k, v in new_m.items():
                if v is not None and old_m.get(k) is None:
                    old_m[k] = v
            merged[uuid]['sleep_metrics'] = old_m

    sorted_records = sorted(merged.values(), key=lambda r: r.get('start_local', ''))

    if dry_run:
        print(f"[dry-run] Would write {len(sorted_records)} sleep records ({added} new)")
        return len(sorted_records)

    # Write to both canonical paths
    for path in [all_nights_path, PROCESSED / "sleep_merged.jsonl"]:
        with open(path, 'w') as f:
            for rec in sorted_records:
                f.write(json.dumps(rec) + '\n')

    print(f"Sleep: {len(sorted_records)} records ({added} new) → {all_nights_path}")
    return len(sorted_records)


# ── Stress processing ────────────────────────────────────────────────────────

def process_stress(dry_run: bool = False) -> int:
    """Process Samsung Health stress measurements."""
    records: dict[str, dict] = {}  # datauuid → record
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.stress.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "score": try_int(row.get('max_score') or row.get('score')),
                "comment": row.get('comment', ''),
            }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]

    if dry_run:
        print(f"[dry-run] Would write {len(sorted_recs)} stress records")
        return len(sorted_recs)

    out = PROCESSED / "health_stress.jsonl"
    with open(out, 'w') as f:
        for rec in sorted_recs:
            f.write(json.dumps(rec) + '\n')
    print(f"Stress: {len(sorted_recs)} records → {out}")
    return len(sorted_recs)


# ── Steps processing ─────────────────────────────────────────────────────────

def process_steps(dry_run: bool = False) -> int:
    """Process Samsung Health daily step counts."""
    records: dict[str, dict] = {}  # create_time → record
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.step_daily_trend.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            ts = row.get('create_time', '')[:10]
            if not ts or ts < '2000':
                continue
            count = try_int(row.get('count'))
            distance = try_float(row.get('distance'))
            speed = try_float(row.get('speed'))
            if count is None:
                continue
            records[ts] = {
                "date": ts,
                "steps": count,
                "distance_m": distance,
                "speed_mps": speed,
            }

    sorted_recs = sorted(records.values(), key=lambda r: r['date'])

    if dry_run:
        print(f"[dry-run] Would write {len(sorted_recs)} step records")
        return len(sorted_recs)

    out = PROCESSED / "health_steps.jsonl"
    with open(out, 'w') as f:
        for rec in sorted_recs:
            f.write(json.dumps(rec) + '\n')
    print(f"Steps: {len(sorted_recs)} records → {out}")
    return len(sorted_recs)


# ── HRV processing ───────────────────────────────────────────────────────────

def process_hrv(dry_run: bool = False) -> int:
    """Process Samsung Health HRV (heart rate variability)."""
    records: dict[str, dict] = {}
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.health.hrv.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', row.get('create_time', ''))
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "comment": row.get('comment', ''),
            }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]

    if dry_run:
        print(f"[dry-run] Would write {len(sorted_recs)} HRV records")
        return len(sorted_recs)

    out = PROCESSED / "health_hrv.jsonl"
    with open(out, 'w') as f:
        for rec in sorted_recs:
            f.write(json.dumps(rec) + '\n')
    print(f"HRV: {len(sorted_recs)} records → {out}")
    return len(sorted_recs)


# ── Vitality processing ─────────────────────────────────────────────────────

def process_vitality(dry_run: bool = False) -> int:
    """Process Samsung Health vitality scores."""
    records: dict[str, dict] = {}
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.vitality_score.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            ts = (row.get('create_time') or '')[:10]
            if not ts or ts < '2000':
                continue
            records[ts] = {
                "date": ts,
                "activity_score": try_float(row.get('activity_score')),
                "activity_level": row.get('activity_level', ''),
            }

    sorted_recs = sorted(records.values(), key=lambda r: r['date'])

    if dry_run:
        print(f"[dry-run] Would write {len(sorted_recs)} vitality records")
        return len(sorted_recs)

    out = PROCESSED / "health_vitality.jsonl"
    with open(out, 'w') as f:
        for rec in sorted_recs:
            f.write(json.dumps(rec) + '\n')
    print(f"Vitality: {len(sorted_recs)} records → {out}")
    return len(sorted_recs)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    PROCESSED.mkdir(parents=True, exist_ok=True)

    process_sleep(dry_run)
    process_stress(dry_run)
    process_steps(dry_run)
    process_hrv(dry_run)
    process_vitality(dry_run)


if __name__ == "__main__":
    main()
