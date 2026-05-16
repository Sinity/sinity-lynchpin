"""Samsung Health sleep processor."""

from __future__ import annotations

import json

from lynchpin.cli.health_io import (
    HEALTH_RAW,
    PROCESSED,
    parse_dt,
    parse_gdpr_dt,
    parse_gdpr_offset,
    parse_offset,
    read_gdpr_cloud_csvs,
    read_samsung_csv,
    try_float,
)

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
            datauuid = row.get("datauuid", "")
            if not datauuid:
                continue
            offset_str = row.get("time_offset", "UTC+0000")
            start = parse_dt(row.get("start_time"), offset_str)
            end = parse_dt(row.get("end_time"), offset_str)
            if not start or not end:
                continue

            new_records[datauuid] = {
                "canonical_id": datauuid,
                "source": "samsung_only",
                "start_local": start,
                "end_local": end,
                "duration_minutes": try_float(row.get("sleep_duration")) or 0,
                "device_uuid": row.get("deviceuuid", ""),
                "device_name": "",
                "time_offset_hours": parse_offset(offset_str),
                "sleep_metrics": {
                    "sleep_score": try_float(row.get("sleep_score")),
                    "sleep_duration": try_float(row.get("sleep_duration")),
                    "total_rem_duration": try_float(row.get("total_rem_duration")),
                    "total_light_duration": try_float(row.get("total_light_duration")),
                    "deep_score": try_float(row.get("deep_score")),
                    "rem_score": try_float(row.get("rem_score")),
                    "wake_score": try_float(row.get("wake_score")),
                    "physical_recovery": try_float(row.get("physical_recovery")),
                    "mental_recovery": try_float(row.get("mental_recovery")),
                    "sleep_efficiency": try_float(row.get("efficiency")),
                    "sleep_cycle": try_float(row.get("sleep_cycle")),
                    "movement_awakening": try_float(row.get("movement_awakening")),
                },
                "saa_metrics": None,
                "deltas": None,
                "comment": None,
            }

    # 3. GDPR Sleep Combined
    for row in read_gdpr_cloud_csvs("Sleep Combined"):
        datauuid = row.get("datauuid", "")
        if not datauuid:
            continue
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)
        if not start or not end:
            continue
        if datauuid not in new_records:
            new_records[datauuid] = {
                "canonical_id": datauuid,
                "source": "samsung_only",
                "start_local": start,
                "end_local": end,
                "duration_minutes": try_float(row.get("sleep_duration")) or 0,
                "device_uuid": row.get("deviceuuid", ""),
                "device_name": "",
                "time_offset_hours": parse_gdpr_offset(offset_ms),
                "sleep_metrics": {
                    "sleep_score": try_float(row.get("sleep_score")),
                    "sleep_duration": try_float(row.get("sleep_duration")),
                    "total_rem_duration": try_float(row.get("total_rem_duration")),
                    "total_light_duration": try_float(row.get("total_light_duration")),
                    "deep_score": try_float(row.get("deep_score")),
                    "rem_score": try_float(row.get("rem_score")),
                    "wake_score": try_float(row.get("wake_score")),
                    "physical_recovery": try_float(row.get("physical_recovery")),
                    "mental_recovery": try_float(row.get("mental_recovery")),
                    "sleep_efficiency": try_float(row.get("efficiency")),
                    "sleep_cycle": try_float(row.get("sleep_cycle")),
                    "movement_awakening": try_float(row.get("movement_awakening")),
                },
                "saa_metrics": None,
                "deltas": None,
                "comment": None,
            }
        else:
            # Enrich existing metrics from GDPR
            old_m = new_records[datauuid].get("sleep_metrics") or {}
            gdpr_m = {
                "sleep_score": try_float(row.get("sleep_score")),
                "sleep_duration": try_float(row.get("sleep_duration")),
                "total_rem_duration": try_float(row.get("total_rem_duration")),
                "total_light_duration": try_float(row.get("total_light_duration")),
                "deep_score": try_float(row.get("deep_score")),
                "rem_score": try_float(row.get("rem_score")),
                "wake_score": try_float(row.get("wake_score")),
                "physical_recovery": try_float(row.get("physical_recovery")),
                "mental_recovery": try_float(row.get("mental_recovery")),
                "sleep_efficiency": try_float(row.get("efficiency")),
                "sleep_cycle": try_float(row.get("sleep_cycle")),
                "movement_awakening": try_float(row.get("movement_awakening")),
            }
            for k, v in gdpr_m.items():
                if v is not None and old_m.get(k) is None:
                    old_m[k] = v
            new_records[datauuid]["sleep_metrics"] = old_m

    # 4. GDPR Sleep (basic sleep records without combined metrics)
    for row in read_gdpr_cloud_csvs("Sleep"):
        datauuid = row.get("datauuid", "")
        if not datauuid or datauuid in new_records:
            continue
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)
        if not start or not end:
            continue
        new_records[datauuid] = {
            "canonical_id": datauuid,
            "source": "samsung_only",
            "start_local": start,
            "end_local": end,
            "duration_minutes": try_float(row.get("sleep_duration")) or 0,
            "device_uuid": row.get("deviceuuid", ""),
            "device_name": "",
            "time_offset_hours": parse_gdpr_offset(offset_ms),
            "sleep_metrics": {
                "sleep_score": try_float(row.get("sleep_score")),
                "sleep_duration": try_float(row.get("sleep_duration")),
                "total_rem_duration": None,
                "total_light_duration": None,
                "deep_score": None,
                "rem_score": None,
                "wake_score": None,
                "physical_recovery": try_float(row.get("physical_recovery")),
                "mental_recovery": try_float(row.get("mental_recovery")),
                "sleep_efficiency": try_float(row.get("efficiency")),
                "sleep_cycle": try_float(row.get("sleep_cycle")),
                "movement_awakening": try_float(row.get("movement_awakening")),
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
            old_m = merged[uuid].get("sleep_metrics") or {}
            new_m = rec.get("sleep_metrics") or {}
            for k, v in new_m.items():
                if v is not None and old_m.get(k) is None:
                    old_m[k] = v
            merged[uuid]["sleep_metrics"] = old_m

    sorted_records = sorted(merged.values(), key=lambda r: r.get("start_local", ""))

    if dry_run:
        print(
            f"[dry-run] Would write {len(sorted_records)} sleep records ({added} new)"
        )
        return len(sorted_records)

    # Write to both canonical paths
    for path in [all_nights_path, PROCESSED / "sleep_merged.jsonl"]:
        with open(path, "w") as f:
            for rec in sorted_records:
                f.write(json.dumps(rec) + "\n")

    print(f"Sleep: {len(sorted_records)} records ({added} new) -> {all_nights_path}")
    return len(sorted_records)


__all__ = ["process_sleep"]
