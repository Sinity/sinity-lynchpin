"""GDPR-only Samsung Health processors."""

from __future__ import annotations

from datetime import datetime

from lynchpin.cli.health_io import (
    parse_gdpr_dt,
    read_gdpr_cloud_csvs,
    try_float,
    try_int,
    try_json,
    write_jsonl,
)

# ── Respiratory rate processing ─────────────────────────────────────────────


def process_respiratory_rate(dry_run: bool = False) -> int:
    """Process Samsung Health respiratory rate from GDPR cloud."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Health Respiratory Rate"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        avg_rate = try_float(row.get("average"))
        if avg_rate is None:
            continue
        records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "avg_rate": avg_rate,
            "lower_limit": try_float(row.get("lower_limit")),
            "upper_limit": try_float(row.get("upper_limit")),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(
        sorted_recs, "health_respiratory.jsonl", "Respiratory rate", dry_run
    )


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
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        stage_code = try_int(row.get("stage"))
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)

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
            "stage": SLEEP_STAGE_MAP.get(stage_code, f"unknown_{stage_code}")
            if stage_code
            else None,
            "stage_code": stage_code,
            "sleep_id": row.get("sleep_id", ""),
            "duration_minutes": duration_minutes,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(
        sorted_recs, "health_sleep_stages.jsonl", "Sleep stages", dry_run
    )


# ── Activity summary (GDPR only) ────────────────────────────────────────────


def process_activity_summary(dry_run: bool = False) -> int:
    """Process GDPR Activity Day Summary data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Activity Day Summary"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        day_time = row.get("day_time", "")
        date = day_time[:10] if day_time else (row.get("create_time") or "")[:10]
        if not date or date < "2000":
            continue

        extra = try_json(row.get("extra_data", ""))
        records[uuid] = {
            "datauuid": uuid,
            "date": date,
            "active_time_ms": try_int(row.get("active_time")),
            "calories": try_float(row.get("calorie")),
            "step_count": try_int(row.get("step_count")),
            "distance_m": try_float(row.get("distance")),
            "walk_time_ms": try_int(row.get("walk_time")),
            "run_time_ms": try_int(row.get("run_time")),
            "score": try_int(row.get("score")),
            "goal": try_int(row.get("goal")),
            "extra_data": extra,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("date") or "")
    return write_jsonl(
        sorted_recs, "health_activity_summary.jsonl", "Activity summary", dry_run
    )


# ── Movement (GDPR only) ────────────────────────────────────────────────────


def process_movement(dry_run: bool = False) -> int:
    """Process GDPR Health Movement data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Health Movement"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)

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

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_movement.jsonl", "Movement", dry_run)


# ── ECG (GDPR only) ─────────────────────────────────────────────────────────


def process_ecg(dry_run: bool = False) -> int:
    """Process GDPR Electrocardiogram data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Electrocardiogram 2"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        records[uuid] = {
            "datauuid": uuid,
            "start_time": parse_gdpr_dt(row.get("start_time"), offset_ms),
            "end_time": parse_gdpr_dt(row.get("end_time"), offset_ms),
            "mean_heart_rate": try_float(row.get("mean_heart_rate")),
            "sample_count": try_int(row.get("sample_count")),
            "sample_frequency": try_int(row.get("sample_frequency")),
            "data_key": row.get("data_key", ""),
            "data_mime": row.get("data_mime", ""),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_ecg.jsonl", "ECG", dry_run)


# ── Calories burned (GDPR only) ─────────────────────────────────────────────


def process_calories(dry_run: bool = False) -> int:
    """Process GDPR Calories Burned data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Calories Burned"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        day_time = row.get("day_time", "")
        date = day_time[:10] if day_time else (row.get("create_time") or "")[:10]
        if not date or date < "2000":
            continue

        extra = try_json(row.get("extra_data", ""))
        records[uuid] = {
            "datauuid": uuid,
            "date": date,
            "active_calorie": try_float(row.get("active_calorie")),
            "rest_calorie": try_float(row.get("rest_calorie")),
            "tef_calorie": try_float(row.get("tef_calorie")),
            "active_time_ms": try_int(row.get("active_time")),
            "extra_data": extra,
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("date") or "")
    return write_jsonl(sorted_recs, "health_calories.jsonl", "Calories", dry_run)


# ── Naps (GDPR only) ────────────────────────────────────────────────────────


def process_naps(dry_run: bool = False) -> int:
    """Process GDPR Shealth Vitality Nap Data."""
    records: dict[str, dict] = {}

    for row in read_gdpr_cloud_csvs("Shealth Vitality Nap Data"):
        uuid = row.get("datauuid", "")
        if not uuid:
            continue
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)

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
            "before_vitality": try_float(row.get("score_before")),
            "after_vitality": try_float(row.get("score_after")),
            "vitality_day_time": row.get("vitality_day_time", ""),
        }

    sorted_recs = sorted(records.values(), key=lambda r: r.get("start_time") or "")
    sorted_recs = [r for r in sorted_recs if r.get("start_time")]
    return write_jsonl(sorted_recs, "health_naps.jsonl", "Naps", dry_run)


__all__ = [
    "process_activity_summary",
    "process_calories",
    "process_ecg",
    "process_movement",
    "process_naps",
    "process_respiratory_rate",
    "process_sleep_stages",
]
