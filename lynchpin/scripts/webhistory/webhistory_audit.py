"""Audit helpers for canonical webhistory materializations."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...sources.web import iter_raw_file_entries, normalize_url
from .webhistory_compare import iter_events


def audit_webhistory(
    *,
    raw_root: Path,
    canonical: Path,
    merged: Path,
    tolerance: int,
    sample: int,
) -> dict[str, object]:
    expected_keys, expected_sources, per_file = _simulate_dedup_key_data(raw_root, tolerance)
    canonical_keys, canonical_sources = _load_event_key_data(canonical)
    merged_keys, merged_sources = _load_event_key_data(merged) if merged.exists() else ([], {})

    expected_counter = Counter(expected_keys)
    canonical_counter = Counter(canonical_keys)
    merged_counter = Counter(merged_keys)

    expected_missing_canonical, expected_missing_canonical_sample = _counter_diff_sample(
        expected_counter - canonical_counter,
        expected_sources,
        sample,
    )
    canonical_extra, canonical_extra_sample = _counter_diff_sample(
        canonical_counter - expected_counter,
        canonical_sources,
        sample,
    )
    canonical_missing_merged, canonical_missing_merged_sample = _counter_diff_sample(
        canonical_counter - merged_counter,
        canonical_sources,
        sample,
    )
    merged_extra, merged_extra_sample = _counter_diff_sample(
        merged_counter - canonical_counter,
        merged_sources,
        sample,
    )
    expected_missing_merged, expected_missing_merged_sample = _counter_diff_sample(
        expected_counter - merged_counter,
        expected_sources,
        sample,
    )
    merged_extra_vs_expected, merged_extra_vs_expected_sample = _counter_diff_sample(
        merged_counter - expected_counter,
        merged_sources,
        sample,
    )

    return {
        "raw_root": str(raw_root),
        "canonical": str(canonical),
        "merged": str(merged),
        "tolerance_seconds": tolerance,
        "raw_file_count": len(per_file),
        "raw_rows": sum(int(row["rows"]) for row in per_file),
        "simulated_dedup_count": len(expected_keys),
        "simulated_duplicate_rows": sum(int(row["duplicates"]) for row in per_file),
        "simulated_range": _key_range(expected_keys),
        "canonical_count": len(canonical_keys),
        "canonical_range": _key_range(canonical_keys),
        "canonical_duplicate_keys": _duplicate_count(canonical_counter),
        "merged_count": len(merged_keys),
        "merged_range": _key_range(merged_keys),
        "merged_duplicate_keys": _duplicate_count(merged_counter),
        "expected_vs_canonical": {
            "missing": expected_missing_canonical,
            "missing_sample": expected_missing_canonical_sample,
            "extra": canonical_extra,
            "extra_sample": canonical_extra_sample,
        },
        "canonical_vs_merged": {
            "missing": canonical_missing_merged,
            "missing_sample": canonical_missing_merged_sample,
            "extra": merged_extra,
            "extra_sample": merged_extra_sample,
        },
        "expected_vs_merged": {
            "missing": expected_missing_merged,
            "missing_sample": expected_missing_merged_sample,
            "extra": merged_extra_vs_expected,
            "extra_sample": merged_extra_vs_expected_sample,
        },
        "per_file": per_file,
    }


def _load_event_key_data(path: Path) -> tuple[list[tuple[str, str]], dict[tuple[str, str], str]]:
    keys: list[tuple[str, str]] = []
    sources: dict[tuple[str, str], str] = {}
    for event in iter_events(path):
        norm = normalize_url(event.url or "")
        iso_time = _to_iso_time(event.timestamp)
        key = (norm, iso_time)
        keys.append(key)
        sources.setdefault(key, event.source)
    return keys, sources


def _simulate_dedup_key_data(
    raw_root: Path,
    tolerance: int,
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], str], list[dict[str, object]]]:
    seen: dict[tuple[str, datetime], bool] = {}
    keys: list[tuple[str, str]] = []
    sources: dict[tuple[str, str], str] = {}
    per_file: list[dict[str, object]] = []

    for path, entries in iter_raw_file_entries(root=raw_root):
        row_count = 0
        kept_count = 0
        duplicate_count = 0
        for entry in entries:
            row_count += 1
            dt = entry.timestamp.astimezone(timezone.utc)
            norm = normalize_url(entry.url)
            base = dt.replace(microsecond=0)
            duplicate = False
            for delta in range(-tolerance, tolerance + 1):
                if (norm, base + timedelta(seconds=delta)) in seen:
                    duplicate = True
                    duplicate_count += 1
                    break
            if duplicate:
                continue
            seen[(norm, base)] = True
            key = (norm, _to_iso_time(dt))
            keys.append(key)
            sources.setdefault(key, str(path))
            kept_count += 1
        per_file.append(
            {
                "file": str(path),
                "rows": row_count,
                "kept": kept_count,
                "duplicates": duplicate_count,
            }
        )
    return keys, sources, per_file


def _key_range(keys: list[tuple[str, str]]) -> dict[str, str | None]:
    if not keys:
        return {"start": None, "end": None}
    times = [iso_time for _norm, iso_time in keys]
    return {"start": min(times), "end": max(times)}


def _counter_diff_sample(
    diff: Counter[tuple[str, str]],
    sources: dict[tuple[str, str], str],
    sample: int,
) -> tuple[int, list[dict[str, object]]]:
    total = sum(diff.values())
    rows: list[dict[str, object]] = []
    for (norm, iso_time), count in diff.items():
        rows.append(
            {
                "norm": norm,
                "iso_time": iso_time,
                "count": count,
                "source": sources.get((norm, iso_time)),
            }
        )
        if len(rows) >= sample:
            break
    return total, rows


def _duplicate_count(counter: Counter[tuple[str, str]]) -> int:
    return sum(count - 1 for count in counter.values() if count > 1)


def _to_iso_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
