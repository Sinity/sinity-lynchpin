#!/usr/bin/env python3
"""Process Samsung Health exports into unified health JSONL.

Reads all Samsung Health export directories (including unexpanded archives)
AND the Samsung GDPR cloud export, deduplicates by datauuid (record with more
populated fields wins), and writes:

  In-app + GDPR merged:
  - health_sleep.jsonl              — all sleep records (naps + full sleep)
  - health_stress.jsonl             — stress measurements
  - health_steps.jsonl              — daily step counts
  - health_hrv.jsonl                — heart rate variability with SDNN/RMSSD
  - health_vitality.jsonl           — daily vitality scores
  - health_weight.jsonl             — body composition measurements
  - health_skin_temperature.jsonl   — skin temperature readings
  - health_floors.jsonl             — floors climbed
  - health_mood.jsonl               — mood entries (1-5 scale)
  - health_snoring.jsonl            — sleep snoring durations
  - health_heart_rate.jsonl         — heart rate measurements
  - health_spo2.jsonl               — blood oxygen saturation

  GDPR-only categories:
  - health_sleep_stages.jsonl       — per-stage sleep data (awake/light/deep/REM)
  - health_activity_summary.jsonl   — daily activity summaries
  - health_movement.jsonl           — movement episodes
  - health_ecg.jsonl                — electrocardiogram readings
  - health_calories.jsonl           — daily calories burned
  - health_naps.jsonl               — nap data with vitality scores

Usage:
    python -m lynchpin.scripts.process_health [--dry-run]
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEALTH_RAW = Path("/realm/data/exports/health/raw/samsung-health")
SAA_RAW = Path("/realm/data/exports/health/raw/sleep-as-android")
GDPR_CLOUD_DIR = Path("/realm/data/exports/health/raw/samsung-gdpr-cloud")
PROCESSED = Path("/realm/data/exports/health/processed")

# Bump CSV field size limit for exercise/ECG rows with large embedded JSON
csv.field_size_limit(sys.maxsize)


# ── Parsing helpers ──────────────────────────────────────────────────────────

def read_samsung_csv(path: Path) -> list[dict]:
    """Samsung Health CSV: line 1 = metadata, line 2 = header, rest = data."""
    with open(path, encoding='utf-8-sig') as f:
        f.readline()  # skip metadata
        reader = csv.DictReader(f)
        return [row for row in reader if any(row.values())]


def read_samsung_csv_bytes(data: bytes) -> list[dict]:
    """Read Samsung Health CSV from in-memory bytes (for archive extraction)."""
    text = data.decode('utf-8-sig')
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    # Skip metadata line
    remaining = ''.join(lines[1:])
    reader = csv.DictReader(io.StringIO(remaining))
    return [row for row in reader if any(row.values())]


def read_gdpr_cloud_csvs(category_name: str) -> list[dict]:
    """Read all paginated CSVs from a GDPR cloud category directory.

    Each category dir contains Category.csv, Category(1).csv, ..., Category(N).csv.
    CSVs have UTF-8 BOM, standard header on line 1, 200 rows per file.
    Returns concatenated, deduplicated rows (by datauuid if present).
    """
    category_dir = GDPR_CLOUD_DIR / category_name
    if not category_dir.is_dir():
        return []

    # Collect all CSV files in the directory
    csv_files = sorted(category_dir.glob("*.csv"), key=_gdpr_csv_sort_key)
    all_rows: list[dict] = []
    for csv_file in csv_files:
        with open(csv_file, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if any(row.values()):
                    all_rows.append(row)

    # Deduplicate by datauuid if the column exists
    if all_rows and 'datauuid' in all_rows[0]:
        seen: dict[str, dict] = {}
        for row in all_rows:
            uuid = row.get('datauuid', '')
            if uuid:
                existing = seen.get(uuid)
                if existing is None or _non_empty_count(row) > _non_empty_count(existing):
                    seen[uuid] = row
            else:
                seen[id(row)] = row  # type: ignore[arg-type]
        return list(seen.values())

    return all_rows


def _gdpr_csv_sort_key(path: Path) -> tuple[int, str]:
    """Sort key: base file first (index 0), then by numeric suffix."""
    name = path.stem
    m = re.search(r'\((\d+)\)$', name)
    if m:
        return (int(m.group(1)), name)
    return (-1, name)  # base file sorts first


def _non_empty_count(row: dict) -> int:
    """Count non-empty, non-None values in a row."""
    return sum(1 for v in row.values() if v is not None and v != '')


def parse_offset(offset_str: str) -> float:
    """Parse 'UTC+0200' -> 2.0 hours."""
    if not offset_str or not offset_str.startswith("UTC"):
        return 0.0
    sign = 1 if '+' in offset_str else -1
    num = offset_str.replace("UTC", "").replace("+", "").replace("-", "")
    if len(num) == 4:
        return sign * (int(num[:2]) + int(num[2:]) / 60)
    return sign * float(num) if num else 0.0


def parse_gdpr_offset(offset_ms_str: str) -> float:
    """Parse GDPR millisecond offset to hours. e.g. '3600000' -> 1.0, '7200000' -> 2.0."""
    if not offset_ms_str:
        return 0.0
    try:
        return int(offset_ms_str) / 3_600_000
    except (ValueError, TypeError):
        return 0.0


def parse_dt(value: str | None, offset_str: str = "UTC+0000") -> str | None:
    """Parse Samsung datetime -> ISO 8601 with timezone."""
    if not value or value.startswith("1970"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
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


def parse_gdpr_dt(value: str | None, offset_ms_str: str = "0") -> str | None:
    """Parse GDPR datetime with millisecond offset -> ISO 8601.

    Handles both human-readable dates ('2022-09-02 00:03') and epoch
    millisecond timestamps ('1679440307021').
    """
    if not value or value.startswith("1970"):
        return None
    hours = parse_gdpr_offset(offset_ms_str)
    tz = timezone(timedelta(hours=hours))

    # Try epoch-ms first (pure digits, 13+ chars)
    if value.isdigit() and len(value) >= 13:
        try:
            epoch_s = int(value) / 1000
            dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc).astimezone(tz)
            return dt.isoformat()
        except (ValueError, OverflowError, OSError):
            pass

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        return None
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


def try_json(v: str | None) -> object | None:
    """Try to parse a JSON string, return None on failure."""
    if not v:
        return None
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return None


def iter_export_dirs() -> list[Path]:
    """Yield sorted export directories (only actual directories, not archives)."""
    return sorted(d for d in HEALTH_RAW.iterdir() if d.is_dir() and list(d.glob("*.csv")))


def extract_csv_from_zip(zip_path: Path, pattern: str) -> list[dict]:
    """Extract and parse a Samsung CSV matching pattern from a zip archive."""
    rows: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if pattern in name and name.endswith('.csv'):
                    data = zf.read(name)
                    rows.extend(read_samsung_csv_bytes(data))
    except (zipfile.BadZipFile, KeyError):
        pass
    return rows


def extract_csv_from_tar(tar_path: Path, pattern: str) -> list[dict]:
    """Extract and parse a Samsung CSV matching pattern from a tar(.gz) archive."""
    rows: list[dict] = []
    try:
        with tarfile.open(tar_path, 'r:gz') as tf:
            for member in tf.getmembers():
                if pattern in member.name and member.name.endswith('.csv'):
                    f = tf.extractfile(member)
                    if f:
                        rows.extend(read_samsung_csv_bytes(f.read()))
    except (tarfile.TarError, OSError):
        pass
    return rows


def iter_archive_csv_rows(pattern: str) -> list[dict]:
    """Read CSV rows matching pattern from unexpanded archives (zip + tar)."""
    rows: list[dict] = []
    zip_path = HEALTH_RAW / "2025-01-21" / "samsung_health_data.zip"
    if zip_path.exists():
        rows.extend(extract_csv_from_zip(zip_path, pattern))
    tar_path = HEALTH_RAW / "2025-04-25" / "samsunghealth.tar"
    if tar_path.exists():
        rows.extend(extract_csv_from_tar(tar_path, pattern))
    return rows


def merge_by_datauuid(base: dict[str, dict], incoming: dict[str, dict]) -> dict[str, dict]:
    """Merge incoming records into base, preferring the record with more populated fields."""
    merged = dict(base)
    for uuid, rec in incoming.items():
        existing = merged.get(uuid)
        if existing is None:
            merged[uuid] = rec
        else:
            # Keep the record with more non-None/non-empty values
            existing_count = sum(1 for v in existing.values() if v is not None and v != '' and v != 0)
            incoming_count = sum(1 for v in rec.values() if v is not None and v != '' and v != 0)
            if incoming_count > existing_count:
                merged[uuid] = rec
    return merged


def write_jsonl(records: list[dict], filename: str, label: str, dry_run: bool) -> int:
    """Write records to JSONL file, or report dry-run count."""
    if dry_run:
        print(f"[dry-run] Would write {len(records)} {label} records")
        return len(records)
    out = PROCESSED / filename
    with open(out, 'w') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')
    print(f"{label}: {len(records)} records -> {out}")
    return len(records)


# ── Sleep processing ─────────────────────────────────────────────────────────

def process_sleep(dry_run: bool = False) -> int:
    """Process all Samsung Health sleep exports + SAA + GDPR into unified JSONL."""
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

    # 2. Process all Samsung Health sleep_combined exports (in-app)
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

    # 3. GDPR Sleep Combined
    for row in read_gdpr_cloud_csvs("Sleep Combined"):
        datauuid = row.get('datauuid', '')
        if not datauuid:
            continue
        offset_ms = row.get('time_offset', '0')
        start = parse_gdpr_dt(row.get('start_time'), offset_ms)
        end = parse_gdpr_dt(row.get('end_time'), offset_ms)
        if not start or not end:
            continue
        if datauuid not in new_records:
            new_records[datauuid] = {
                "canonical_id": datauuid,
                "source": "samsung_only",
                "start_local": start,
                "end_local": end,
                "duration_minutes": try_float(row.get('sleep_duration')) or 0,
                "device_uuid": row.get('deviceuuid', ''),
                "device_name": "",
                "time_offset_hours": parse_gdpr_offset(offset_ms),
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
        else:
            # Enrich existing metrics from GDPR
            old_m = new_records[datauuid].get('sleep_metrics') or {}
            gdpr_m = {
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
            }
            for k, v in gdpr_m.items():
                if v is not None and old_m.get(k) is None:
                    old_m[k] = v
            new_records[datauuid]['sleep_metrics'] = old_m

    # 4. GDPR Sleep (basic sleep records without combined metrics)
    for row in read_gdpr_cloud_csvs("Sleep"):
        datauuid = row.get('datauuid', '')
        if not datauuid or datauuid in new_records:
            continue
        offset_ms = row.get('time_offset', '0')
        start = parse_gdpr_dt(row.get('start_time'), offset_ms)
        end = parse_gdpr_dt(row.get('end_time'), offset_ms)
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
            "time_offset_hours": parse_gdpr_offset(offset_ms),
            "sleep_metrics": {
                "sleep_score": try_float(row.get('sleep_score')),
                "sleep_duration": try_float(row.get('sleep_duration')),
                "total_rem_duration": None,
                "total_light_duration": None,
                "deep_score": None,
                "rem_score": None,
                "wake_score": None,
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

    # 5. Merge: existing (with SAA fusion) take priority
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

    print(f"Sleep: {len(sorted_records)} records ({added} new) -> {all_nights_path}")
    return len(sorted_records)


# ── Stress processing ────────────────────────────────────────────────────────

def process_stress(dry_run: bool = False) -> int:
    """Process Samsung Health stress measurements (in-app + GDPR)."""
    records: dict[str, dict] = {}  # datauuid -> record

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = [
            c for c in export_dir.glob("com.samsung.shealth.stress.*.csv")
            if '.histogram.' not in c.name
        ]
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

    # GDPR cloud: Stress Internal Data
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Stress Internal Data"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "score": try_int(row.get('score') or row.get('max')),
            "comment": row.get('comment', ''),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_stress.jsonl", "Stress", dry_run)


# ── Steps processing ─────────────────────────────────────────────────────────

def process_steps(dry_run: bool = False) -> int:
    """Process Samsung Health daily step counts."""
    records: dict[str, dict] = {}  # create_time -> record
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
    return write_jsonl(sorted_recs, "health_steps.jsonl", "Steps", dry_run)


# ── HRV processing ───────────────────────────────────────────────────────────

def _load_hrv_binning(export_dir: Path, binning_ref: str) -> dict | None:
    """Load HRV companion JSON and compute session averages for SDNN/RMSSD.

    The CSV references files like 'uuid.binning_data.json' which live under
    jsons/com.samsung.health.hrv/{first-char-of-uuid}/.
    """
    if not binning_ref:
        return None
    uuid_prefix = binning_ref[0]
    json_path = export_dir / "jsons" / "com.samsung.health.hrv" / uuid_prefix / binning_ref
    if not json_path.exists():
        return None
    try:
        with open(json_path) as f:
            windows = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not windows or not isinstance(windows, list):
        return None
    sdnn_vals = [w['sdnn'] for w in windows if 'sdnn' in w and w['sdnn'] is not None]
    rmssd_vals = [w['rmssd'] for w in windows if 'rmssd' in w and w['rmssd'] is not None]
    n = len(windows)
    return {
        "sdnn_avg": round(sum(sdnn_vals) / len(sdnn_vals), 2) if sdnn_vals else None,
        "rmssd_avg": round(sum(rmssd_vals) / len(rmssd_vals), 2) if rmssd_vals else None,
        "n_windows": n,
    }


def _parse_hrv_binning_json(binning_str: str) -> dict | None:
    """Parse inline HRV binning_data JSON from GDPR CSV and compute SDNN/RMSSD averages."""
    if not binning_str:
        return None
    try:
        windows = json.loads(binning_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not windows or not isinstance(windows, list):
        return None
    sdnn_vals = [w['sdnn'] for w in windows if 'sdnn' in w and w['sdnn'] is not None]
    rmssd_vals = [w['rmssd'] for w in windows if 'rmssd' in w and w['rmssd'] is not None]
    n = len(windows)
    return {
        "sdnn_avg": round(sum(sdnn_vals) / len(sdnn_vals), 2) if sdnn_vals else None,
        "rmssd_avg": round(sum(rmssd_vals) / len(rmssd_vals), 2) if rmssd_vals else None,
        "n_windows": n,
    }


def process_hrv(dry_run: bool = False) -> int:
    """Process Samsung Health HRV with companion JSON SDNN/RMSSD data + GDPR."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.health.hrv.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            binning_ref = row.get('binning_data', '')
            binning = _load_hrv_binning(export_dir, binning_ref)
            rec = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "end_time": parse_dt(row.get('end_time'), offset),
                "sdnn_avg": binning["sdnn_avg"] if binning else None,
                "rmssd_avg": binning["rmssd_avg"] if binning else None,
                "n_windows": binning["n_windows"] if binning else None,
            }
            # Keep enriched record if we already have one with binning data
            existing = records.get(uuid)
            if existing and existing.get('sdnn_avg') is not None and rec['sdnn_avg'] is None:
                continue
            records[uuid] = rec

    # GDPR cloud: Health HRV (binning_data is inline JSON)
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Health HRV"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        binning = _parse_hrv_binning_json(row.get('binning_data', ''))
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "sdnn_avg": binning["sdnn_avg"] if binning else None,
            "rmssd_avg": binning["rmssd_avg"] if binning else None,
            "n_windows": binning["n_windows"] if binning else None,
        }

    # Merge: prefer whichever has binning data
    for uuid, gdpr_rec in gdpr_records.items():
        existing = records.get(uuid)
        if existing is None:
            records[uuid] = gdpr_rec
        elif existing.get('sdnn_avg') is None and gdpr_rec.get('sdnn_avg') is not None:
            records[uuid] = gdpr_rec

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_hrv.jsonl", "HRV", dry_run)


# ── Vitality processing ─────────────────────────────────────────────────────

def process_vitality(dry_run: bool = False) -> int:
    """Process Samsung Health vitality scores (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports (keyed by date)
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

    # GDPR cloud: Health Vitality Score (much richer, keyed by datauuid)
    gdpr_by_date: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Health Vitality Score"):
        day_time = row.get('day_time', '')
        ts = day_time[:10] if day_time else (row.get('create_time') or '')[:10]
        if not ts or ts < '2000':
            continue
        gdpr_by_date[ts] = {
            "date": ts,
            "activity_score": try_float(row.get('activity_score')),
            "activity_level": row.get('activity_level', ''),
            "total_score": try_float(row.get('total_score')),
            "sleep_score": try_float(row.get('sleep_score')),
            "shr_score": try_float(row.get('shr_score')),
            "shrv_score": try_float(row.get('shrv_score')),
        }

    # Merge: GDPR has richer data
    for ts, gdpr_rec in gdpr_by_date.items():
        existing = records.get(ts)
        if existing is None:
            records[ts] = gdpr_rec
        else:
            # Enrich with GDPR fields
            for k, v in gdpr_rec.items():
                if v is not None and v != '' and (k not in existing or existing[k] is None or existing[k] == ''):
                    existing[k] = v

    sorted_recs = sorted(records.values(), key=lambda r: r['date'])
    return write_jsonl(sorted_recs, "health_vitality.jsonl", "Vitality", dry_run)


# ── Weight processing ────────────────────────────────────────────────────────

def process_weight(dry_run: bool = False) -> int:
    """Process Samsung Health weight/body composition measurements (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.health.weight.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            weight = try_float(row.get('weight'))
            if weight is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "weight_kg": weight,
                "body_fat_pct": try_float(row.get('body_fat')),
                "muscle_mass_kg": try_float(row.get('muscle_mass')),
                "skeletal_muscle_pct": try_float(row.get('skeletal_muscle')),
                "basal_metabolic_rate": try_float(row.get('basal_metabolic_rate')),
                "body_fat_mass_kg": try_float(row.get('body_fat_mass')),
                "total_body_water_pct": try_float(row.get('total_body_water')),
            }

    # GDPR cloud: Weight
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Weight"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        weight = try_float(row.get('weight'))
        if weight is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "weight_kg": weight,
            "body_fat_pct": try_float(row.get('body_fat')),
            "muscle_mass_kg": try_float(row.get('skeletal_muscle_mass')),
            "skeletal_muscle_pct": try_float(row.get('skeletal_muscle')),
            "basal_metabolic_rate": try_float(row.get('basal_metabolic_rate')),
            "body_fat_mass_kg": try_float(row.get('body_fat_mass')),
            "total_body_water_pct": try_float(row.get('total_body_water')),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_weight.jsonl", "Weight", dry_run)


# ── Skin temperature processing ──────────────────────────────────────────────

def process_skin_temperature(dry_run: bool = False) -> int:
    """Process Samsung Health skin temperature readings (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.health.skin_temperature.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            temp = try_float(row.get('temperature'))
            if temp is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "temperature": temp,
                "min": try_float(row.get('min')),
                "max": try_float(row.get('max')),
            }

    # GDPR cloud: Samsung Health Skin Temperature
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Samsung Health Skin Temperature"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        temp = try_float(row.get('temperature'))
        if temp is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "temperature": temp,
            "min": try_float(row.get('min')),
            "max": try_float(row.get('max')),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_skin_temperature.jsonl", "Skin temp", dry_run)


# ── Floors climbed processing ────────────────────────────────────────────────

def process_floors(dry_run: bool = False) -> int:
    """Process Samsung Health floors climbed (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.health.floors_climbed.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            floor = try_float(row.get('floor'))
            if floor is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "end_time": parse_dt(row.get('end_time'), offset),
                "floor": floor,
            }

    # GDPR cloud: Floors Climbed
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Floors Climbed"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        floor = try_float(row.get('floor'))
        if floor is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "floor": floor,
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_floors.jsonl", "Floors", dry_run)


# ── Mood processing ──────────────────────────────────────────────────────────

def process_mood(dry_run: bool = False) -> int:
    """Process Samsung Health mood entries (1-5 scale) (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.mood.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get('datauuid', '')
            if not uuid:
                continue
            offset = row.get('time_offset', 'UTC+0000')
            mood = try_int(row.get('mood_type'))
            if mood is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get('start_time'), offset),
                "mood_type": mood,
            }

    # GDPR cloud: Shealth Mood
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Shealth Mood"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        mood = try_int(row.get('mood_type'))
        if mood is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "mood_type": mood,
            "emotions": row.get('emotions', ''),
            "factors": row.get('factors', ''),
            "notes": row.get('notes', ''),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_mood.jsonl", "Mood", dry_run)


# ── Snoring processing ───────────────────────────────────────────────────────

def process_snoring(dry_run: bool = False) -> int:
    """Process Samsung Health sleep snoring data (in-app + GDPR)."""
    records: dict[str, dict] = {}

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.sleep_snoring.*.csv"))
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
                "end_time": parse_dt(row.get('end_time'), offset),
                "duration": try_int(row.get('duration')),
            }

    # GDPR cloud: Sleep Snoring
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Sleep Snoring"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "duration": try_int(row.get('duration')),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_snoring.jsonl", "Snoring", dry_run)


# ── Heart rate processing ────────────────────────────────────────────────────

def _parse_hr_row(row: dict) -> tuple[str, dict] | None:
    """Parse a heart rate row (columns are prefixed with com.samsung.health.heart_rate.)."""
    # Column prefix used in tracker CSV
    p = 'com.samsung.health.heart_rate.'
    uuid = row.get(f'{p}datauuid', row.get('datauuid', ''))
    if not uuid:
        return None
    offset = row.get(f'{p}time_offset', row.get('time_offset', 'UTC+0000'))
    hr = try_float(row.get(f'{p}heart_rate', row.get('heart_rate')))
    if hr is None:
        return None
    return uuid, {
        "datauuid": uuid,
        "start_time": parse_dt(row.get(f'{p}start_time', row.get('start_time')), offset),
        "end_time": parse_dt(row.get(f'{p}end_time', row.get('end_time')), offset),
        "heart_rate": hr,
        "min": try_float(row.get(f'{p}min', row.get('min'))),
        "max": try_float(row.get(f'{p}max', row.get('max'))),
        "heart_beat_count": try_int(row.get(f'{p}heart_beat_count', row.get('heart_beat_count'))),
    }


def process_heart_rate(dry_run: bool = False) -> int:
    """Process Samsung Health heart rate from expanded dirs + archives + GDPR."""
    records: dict[str, dict] = {}

    # 1. Expanded export directories
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.tracker.heart_rate.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            parsed = _parse_hr_row(row)
            if parsed:
                uuid, rec = parsed
                records[uuid] = rec

    # 2. Unexpanded archives (2025-01-21 zip, 2025-04-25 tar)
    for row in iter_archive_csv_rows("tracker.heart_rate."):
        parsed = _parse_hr_row(row)
        if parsed:
            uuid, rec = parsed
            if uuid not in records:  # don't overwrite expanded-dir data
                records[uuid] = rec

    # 3. GDPR cloud: Heart Rate (has binning_data inline)
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Heart Rate"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        hr = try_float(row.get('heart_rate'))
        if hr is None:
            continue
        binning_data = try_json(row.get('binning_data', ''))
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "heart_rate": hr,
            "min": try_float(row.get('min')),
            "max": try_float(row.get('max')),
            "heart_beat_count": try_int(row.get('heart_beat_count')),
            "binning_data": binning_data,
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_heart_rate.jsonl", "Heart rate", dry_run)


# ── SpO2 processing ──────────────────────────────────────────────────────────

def process_spo2(dry_run: bool = False) -> int:
    """Process Samsung Health SpO2 from tracker CSV + GDPR."""
    records: dict[str, dict] = {}
    p = 'com.samsung.health.oxygen_saturation.'

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(export_dir.glob("com.samsung.shealth.tracker.oxygen_saturation.*.csv"))
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get(f'{p}datauuid', row.get('datauuid', ''))
            if not uuid:
                continue
            offset = row.get(f'{p}time_offset', row.get('time_offset', 'UTC+0000'))
            spo2 = try_float(row.get(f'{p}spo2'))
            if spo2 is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get(f'{p}start_time'), offset),
                "end_time": parse_dt(row.get(f'{p}end_time'), offset),
                "spo2": spo2,
                "min": try_float(row.get(f'{p}min')),
                "max": try_float(row.get(f'{p}max')),
                "low_duration": try_int(row.get(f'{p}low_duration')),
            }

    # GDPR cloud: Oxygen Saturation (SpO2)
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Oxygen Saturation (SpO2)"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        spo2 = try_float(row.get('spo2'))
        if spo2 is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "spo2": spo2,
            "min": try_float(row.get('min')),
            "max": try_float(row.get('max')),
            "low_duration": try_int(row.get('low_duration')),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_spo2.jsonl", "SpO2", dry_run)


# ── Respiratory rate processing ─────────────────────────────────────────────

def process_respiratory_rate(dry_run: bool = False) -> int:
    """Process Samsung Health respiratory rate from GDPR cloud."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Health Respiratory Rate"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        avg_rate = try_float(row.get('average'))
        if avg_rate is None:
            continue
        records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "avg_rate": avg_rate,
            "lower_limit": try_float(row.get('lower_limit')),
            "upper_limit": try_float(row.get('upper_limit')),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_respiratory.jsonl", "Respiratory rate", dry_run)


# ── Sleep stages (GDPR only) ────────────────────────────────────────────────

SLEEP_STAGE_MAP = {
    40001: "awake",
    40002: "light",
    40003: "deep",
    40004: "rem",
}


def process_sleep_stages(dry_run: bool = False) -> int:
    """Process GDPR Sleep Stage data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Sleep Stage"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        stage_code = try_int(row.get('stage'))
        start = parse_gdpr_dt(row.get('start_time'), offset_ms)
        end = parse_gdpr_dt(row.get('end_time'), offset_ms)

        # Compute duration in minutes from start/end
        duration_minutes: float | None = None
        if start and end:
            try:
                s = datetime.fromisoformat(start)
                e = datetime.fromisoformat(end)
                duration_minutes = round((e - s).total_seconds() / 60, 2)
            except (ValueError, TypeError):
                pass

        records[uuid] = {
            "datauuid": uuid,
            "start_time": start,
            "end_time": end,
            "stage": SLEEP_STAGE_MAP.get(stage_code, f"unknown_{stage_code}") if stage_code else None,
            "stage_code": stage_code,
            "sleep_id": row.get('sleep_id', ''),
            "duration_minutes": duration_minutes,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_sleep_stages.jsonl", "Sleep stages", dry_run)


# ── Activity summary (GDPR only) ────────────────────────────────────────────

def process_activity_summary(dry_run: bool = False) -> int:
    """Process GDPR Activity Day Summary data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Activity Day Summary"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        day_time = row.get('day_time', '')
        date = day_time[:10] if day_time else (row.get('create_time') or '')[:10]
        if not date or date < '2000':
            continue

        extra = try_json(row.get('extra_data', ''))
        records[uuid] = {
            "datauuid": uuid,
            "date": date,
            "active_time_ms": try_int(row.get('active_time')),
            "calories": try_float(row.get('calorie')),
            "step_count": try_int(row.get('step_count')),
            "distance_m": try_float(row.get('distance')),
            "walk_time_ms": try_int(row.get('walk_time')),
            "run_time_ms": try_int(row.get('run_time')),
            "score": try_int(row.get('score')),
            "goal": try_int(row.get('goal')),
            "extra_data": extra,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('date') or '')
    return write_jsonl(sorted_recs, "health_activity_summary.jsonl", "Activity summary", dry_run)


# ── Movement (GDPR only) ────────────────────────────────────────────────────

def process_movement(dry_run: bool = False) -> int:
    """Process GDPR Health Movement data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Health Movement"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        start = parse_gdpr_dt(row.get('start_time'), offset_ms)
        end = parse_gdpr_dt(row.get('end_time'), offset_ms)

        # Compute duration from start/end
        duration_ms: int | None = None
        if start and end:
            try:
                s = datetime.fromisoformat(start)
                e = datetime.fromisoformat(end)
                duration_ms = int((e - s).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass

        records[uuid] = {
            "datauuid": uuid,
            "start_time": start,
            "end_time": end,
            "duration_ms": duration_ms,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_movement.jsonl", "Movement", dry_run)


# ── ECG (GDPR only) ─────────────────────────────────────────────────────────

def process_ecg(dry_run: bool = False) -> int:
    """Process GDPR Electrocardiogram data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Electrocardiogram 2"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get('start_time'), offset_ms),
            "end_time": parse_gdpr_dt(row.get('end_time'), offset_ms),
            "mean_heart_rate": try_float(row.get('mean_heart_rate')),
            "sample_count": try_int(row.get('sample_count')),
            "sample_frequency": try_int(row.get('sample_frequency')),
            "data_key": row.get('data_key', ''),
            "data_mime": row.get('data_mime', ''),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_ecg.jsonl", "ECG", dry_run)


# ── Calories burned (GDPR only) ─────────────────────────────────────────────

def process_calories(dry_run: bool = False) -> int:
    """Process GDPR Calories Burned data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Calories Burned"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        day_time = row.get('day_time', '')
        date = day_time[:10] if day_time else (row.get('create_time') or '')[:10]
        if not date or date < '2000':
            continue

        extra = try_json(row.get('extra_data', ''))
        records[uuid] = {
            "datauuid": uuid,
            "date": date,
            "active_calorie": try_float(row.get('active_calorie')),
            "rest_calorie": try_float(row.get('rest_calorie')),
            "tef_calorie": try_float(row.get('tef_calorie')),
            "active_time_ms": try_int(row.get('active_time')),
            "extra_data": extra,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('date') or '')
    return write_jsonl(sorted_recs, "health_calories.jsonl", "Calories", dry_run)


# ── Naps (GDPR only) ────────────────────────────────────────────────────────

def process_naps(dry_run: bool = False) -> int:
    """Process GDPR Shealth Vitality Nap Data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Shealth Vitality Nap Data"):
        uuid = row.get('datauuid', '')
        if not uuid:
            continue
        offset_ms = row.get('time_offset', '0')
        start = parse_gdpr_dt(row.get('start_time'), offset_ms)
        end = parse_gdpr_dt(row.get('end_time'), offset_ms)

        # Compute duration
        duration_min: float | None = None
        if start and end:
            try:
                s = datetime.fromisoformat(start)
                e = datetime.fromisoformat(end)
                duration_min = round((e - s).total_seconds() / 60, 2)
            except (ValueError, TypeError):
                pass

        records[uuid] = {
            "datauuid": uuid,
            "start_time": start,
            "end_time": end,
            "duration_min": duration_min,
            "before_vitality": try_float(row.get('score_before')),
            "after_vitality": try_float(row.get('score_after')),
            "vitality_day_time": row.get('vitality_day_time', ''),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get('start_time') or '')
    sorted_recs = [r for r in sorted_recs if r.get('start_time')]
    return write_jsonl(sorted_recs, "health_naps.jsonl", "Naps", dry_run)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    PROCESSED.mkdir(parents=True, exist_ok=True)

    # Existing processors (now with GDPR merge)
    process_sleep(dry_run)
    process_stress(dry_run)
    process_steps(dry_run)
    process_hrv(dry_run)
    process_vitality(dry_run)
    process_weight(dry_run)
    process_skin_temperature(dry_run)
    process_floors(dry_run)
    process_mood(dry_run)
    process_snoring(dry_run)
    process_heart_rate(dry_run)
    process_spo2(dry_run)

    process_respiratory_rate(dry_run)

    # New GDPR-only processors
    process_sleep_stages(dry_run)
    process_activity_summary(dry_run)
    process_movement(dry_run)
    process_ecg(dry_run)
    process_calories(dry_run)
    process_naps(dry_run)


if __name__ == "__main__":
    main()
