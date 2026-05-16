"""Samsung Health merged in-app/GDPR signal processors."""

from __future__ import annotations

import json
from pathlib import Path

from lynchpin.cli.health_io import (
    HEALTH_RAW,
    iter_archive_csv_rows,
    merge_by_datauuid,
    parse_dt,
    parse_gdpr_dt,
    read_gdpr_cloud_csvs,
    read_samsung_csv,
    try_float,
    try_int,
    try_json,
    write_jsonl,
)

# ── Stress processing ────────────────────────────────────────────────────────


def process_stress(dry_run: bool = False) -> int:
    """Process Samsung Health stress measurements (in-app + GDPR)."""
    records: dict[str, dict] = {}  # datauuid -> record

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = [
            c
            for c in export_dir.glob("com.samsung.shealth.stress.*.csv")
            if ".histogram." not in c.name
        ]
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "score": try_int(row.get("max_score") or row.get("score")),
                "comment": row.get("comment", ""),
            }

    # GDPR cloud: Stress Internal Data
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Stress Internal Data"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "score": try_int(row.get("score") or row.get("max")),
            "comment": row.get("comment", ""),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
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
            ts = row.get("create_time", "")[:10]
            if not ts or ts < "2000":
                continue
            count = try_int(row.get("count"))
            distance = try_float(row.get("distance"))
            speed = try_float(row.get("speed"))
            if count is None:
                continue
            records[ts] = {
                "date": ts,
                "steps": count,
                "distance_m": distance,
                "speed_mps": speed,
            }

    sorted_recs = sorted(records.values(), key=lambda r: r["date"])
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
    json_path = (
        export_dir / "jsons" / "com.samsung.health.hrv" / uuid_prefix / binning_ref
    )
    if not json_path.exists():
        return None
    try:
        with open(json_path) as f:
            windows = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not windows or not isinstance(windows, list):
        return None
    sdnn_vals = [w["sdnn"] for w in windows if "sdnn" in w and w["sdnn"] is not None]
    rmssd_vals = [
        w["rmssd"] for w in windows if "rmssd" in w and w["rmssd"] is not None
    ]
    n = len(windows)
    return {
        "sdnn_avg": round(sum(sdnn_vals) / len(sdnn_vals), 2) if sdnn_vals else None,
        "rmssd_avg": round(sum(rmssd_vals) / len(rmssd_vals), 2)
        if rmssd_vals
        else None,
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
    sdnn_vals = [w["sdnn"] for w in windows if "sdnn" in w and w["sdnn"] is not None]
    rmssd_vals = [
        w["rmssd"] for w in windows if "rmssd" in w and w["rmssd"] is not None
    ]
    n = len(windows)
    return {
        "sdnn_avg": round(sum(sdnn_vals) / len(sdnn_vals), 2) if sdnn_vals else None,
        "rmssd_avg": round(sum(rmssd_vals) / len(rmssd_vals), 2)
        if rmssd_vals
        else None,
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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            binning_ref = row.get("binning_data", "")
            binning = _load_hrv_binning(export_dir, binning_ref)
            rec = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "end_time": parse_dt(row.get("end_time"), offset),
                "sdnn_avg": binning["sdnn_avg"] if binning else None,
                "rmssd_avg": binning["rmssd_avg"] if binning else None,
                "n_windows": binning["n_windows"] if binning else None,
            }
            # Keep enriched record if we already have one with binning data
            existing = records.get(uuid)
            if (
                existing
                and existing.get("sdnn_avg") is not None
                and rec["sdnn_avg"] is None
            ):
                continue
            records[uuid] = rec

    # GDPR cloud: Health HRV (binning_data is inline JSON)
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Health HRV"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        binning = _parse_hrv_binning_json(row.get("binning_data", ""))
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "sdnn_avg": binning["sdnn_avg"] if binning else None,
            "rmssd_avg": binning["rmssd_avg"] if binning else None,
            "n_windows": binning["n_windows"] if binning else None,
        }

    # Merge: prefer whichever has binning data
    for uuid, gdpr_rec in gdpr_records.items():
        existing = records.get(uuid)
        if existing is None:
            records[uuid] = gdpr_rec
        elif existing.get("sdnn_avg") is None and gdpr_rec.get("sdnn_avg") is not None:
            records[uuid] = gdpr_rec

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
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
            ts = (row.get("create_time") or "")[:10]
            if not ts or ts < "2000":
                continue
            records[ts] = {
                "date": ts,
                "activity_score": try_float(row.get("activity_score")),
                "activity_level": row.get("activity_level", ""),
            }

    # GDPR cloud: Health Vitality Score (much richer, keyed by datauuid)
    gdpr_by_date: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Health Vitality Score"):
        day_time = row.get("day_time", "")
        ts = day_time[:10] if day_time else (row.get("create_time") or "")[:10]
        if not ts or ts < "2000":
            continue
        gdpr_by_date[ts] = {
            "date": ts,
            "activity_score": try_float(row.get("activity_score")),
            "activity_level": row.get("activity_level", ""),
            "total_score": try_float(row.get("total_score")),
            "sleep_score": try_float(row.get("sleep_score")),
            "shr_score": try_float(row.get("shr_score")),
            "shrv_score": try_float(row.get("shrv_score")),
        }

    # Merge: GDPR has richer data
    for ts, gdpr_rec in gdpr_by_date.items():
        existing = records.get(ts)
        if existing is None:
            records[ts] = gdpr_rec
        else:
            # Enrich with GDPR fields
            for k, v in gdpr_rec.items():
                if (
                    v is not None
                    and v != ""
                    and (k not in existing or existing[k] is None or existing[k] == "")
                ):
                    existing[k] = v

    sorted_recs = sorted(records.values(), key=lambda r: r["date"])
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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            weight = try_float(row.get("weight"))
            if weight is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "weight_kg": weight,
                "body_fat_pct": try_float(row.get("body_fat")),
                "muscle_mass_kg": try_float(row.get("muscle_mass")),
                "skeletal_muscle_pct": try_float(row.get("skeletal_muscle")),
                "basal_metabolic_rate": try_float(row.get("basal_metabolic_rate")),
                "body_fat_mass_kg": try_float(row.get("body_fat_mass")),
                "total_body_water_pct": try_float(row.get("total_body_water")),
            }

    # GDPR cloud: Weight
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Weight"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        weight = try_float(row.get("weight"))
        if weight is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "weight_kg": weight,
            "body_fat_pct": try_float(row.get("body_fat")),
            "muscle_mass_kg": try_float(row.get("skeletal_muscle_mass")),
            "skeletal_muscle_pct": try_float(row.get("skeletal_muscle")),
            "basal_metabolic_rate": try_float(row.get("basal_metabolic_rate")),
            "body_fat_mass_kg": try_float(row.get("body_fat_mass")),
            "total_body_water_pct": try_float(row.get("total_body_water")),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            temp = try_float(row.get("temperature"))
            if temp is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "temperature": temp,
                "min": try_float(row.get("min")),
                "max": try_float(row.get("max")),
            }

    # GDPR cloud: Samsung Health Skin Temperature
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Samsung Health Skin Temperature"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        temp = try_float(row.get("temperature"))
        if temp is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "temperature": temp,
            "min": try_float(row.get("min")),
            "max": try_float(row.get("max")),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(
        sorted_recs, "health_skin_temperature.jsonl", "Skin temp", dry_run
    )


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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            floor = try_float(row.get("floor"))
            if floor is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "end_time": parse_dt(row.get("end_time"), offset),
                "floor": floor,
            }

    # GDPR cloud: Floors Climbed
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Floors Climbed"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        floor = try_float(row.get("floor"))
        if floor is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "floor": floor,
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            mood = try_int(row.get("mood_type"))
            if mood is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "mood_type": mood,
            }

    # GDPR cloud: Shealth Mood
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Shealth Mood"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        mood = try_int(row.get("mood_type"))
        if mood is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "mood_type": mood,
            "emotions": row.get("emotions", ""),
            "factors": row.get("factors", ""),
            "notes": row.get("notes", ""),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
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
            uuid = row.get("datauuid", "")
            if not uuid:
                continue
            offset = row.get("time_offset", "UTC+0000")
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get("start_time"), offset),
                "end_time": parse_dt(row.get("end_time"), offset),
                "duration": try_int(row.get("duration")),
            }

    # GDPR cloud: Sleep Snoring
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Sleep Snoring"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "duration": try_int(row.get("duration")),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_snoring.jsonl", "Snoring", dry_run)


# ── Heart rate processing ────────────────────────────────────────────────────


def _parse_hr_row(row: dict) -> tuple[str, dict] | None:
    """Parse a heart rate row (columns are prefixed with com.samsung.health.heart_rate.)."""
    # Column prefix used in tracker CSV
    p = "com.samsung.health.heart_rate."
    uuid = row.get(f"{p}datauuid", row.get("datauuid", ""))
    if not uuid:
        return None
    offset = row.get(f"{p}time_offset", row.get("time_offset", "UTC+0000"))
    hr = try_float(row.get(f"{p}heart_rate", row.get("heart_rate")))
    if hr is None:
        return None
    return uuid, {
        "datauuid": uuid,
        "start_time": parse_dt(
            row.get(f"{p}start_time", row.get("start_time")), offset
        ),
        "end_time": parse_dt(row.get(f"{p}end_time", row.get("end_time")), offset),
        "heart_rate": hr,
        "min": try_float(row.get(f"{p}min", row.get("min"))),
        "max": try_float(row.get(f"{p}max", row.get("max"))),
        "heart_beat_count": try_int(
            row.get(f"{p}heart_beat_count", row.get("heart_beat_count"))
        ),
    }


def process_heart_rate(dry_run: bool = False) -> int:
    """Process Samsung Health heart rate from expanded dirs + archives + GDPR."""
    records: dict[str, dict] = {}

    # 1. Expanded export directories
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(
            export_dir.glob("com.samsung.shealth.tracker.heart_rate.*.csv")
        )
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
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        hr = try_float(row.get("heart_rate"))
        if hr is None:
            continue
        binning_data = try_json(row.get("binning_data", ""))
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "heart_rate": hr,
            "min": try_float(row.get("min")),
            "max": try_float(row.get("max")),
            "heart_beat_count": try_int(row.get("heart_beat_count")),
            "binning_data": binning_data,
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_heart_rate.jsonl", "Heart rate", dry_run)


# ── SpO2 processing ──────────────────────────────────────────────────────────


def process_spo2(dry_run: bool = False) -> int:
    """Process Samsung Health SpO2 from tracker CSV + GDPR."""
    records: dict[str, dict] = {}
    p = "com.samsung.health.oxygen_saturation."

    # In-app exports
    for export_dir in sorted(HEALTH_RAW.iterdir()):
        if not export_dir.is_dir():
            continue
        candidates = list(
            export_dir.glob("com.samsung.shealth.tracker.oxygen_saturation.*.csv")
        )
        if not candidates:
            continue
        for row in read_samsung_csv(candidates[0]):
            uuid = row.get(f"{p}datauuid", row.get("datauuid", ""))
            if not uuid:
                continue
            offset = row.get(f"{p}time_offset", row.get("time_offset", "UTC+0000"))
            spo2 = try_float(row.get(f"{p}spo2"))
            if spo2 is None:
                continue
            records[uuid] = {
                "datauuid": uuid,
                "start_time": parse_dt(row.get(f"{p}start_time"), offset),
                "end_time": parse_dt(row.get(f"{p}end_time"), offset),
                "spo2": spo2,
                "min": try_float(row.get(f"{p}min")),
                "max": try_float(row.get(f"{p}max")),
                "low_duration": try_int(row.get(f"{p}low_duration")),
            }

    # GDPR cloud: Oxygen Saturation (SpO2)
    gdpr_records: dict[str, dict] = {}
    for row in read_gdpr_cloud_csvs("Oxygen Saturation (SpO2)"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        spo2 = try_float(row.get("spo2"))
        if spo2 is None:
            continue
        gdpr_records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "spo2": spo2,
            "min": try_float(row.get("min")),
            "max": try_float(row.get("max")),
            "low_duration": try_int(row.get("low_duration")),
        }

    records = merge_by_datauuid(records, gdpr_records)

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_spo2.jsonl", "SpO2", dry_run)


__all__ = [
    "process_floors",
    "process_heart_rate",
    "process_hrv",
    "process_mood",
    "process_skin_temperature",
    "process_snoring",
    "process_spo2",
    "process_steps",
    "process_stress",
    "process_vitality",
    "process_weight",
]
