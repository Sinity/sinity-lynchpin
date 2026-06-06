"""Shared Samsung Health export I/O and parsing helpers."""

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
from typing import Any

HEALTH_RAW = Path("/realm/data/exports/health/raw/samsung-health")
SAA_RAW = Path("/realm/data/exports/health/raw/sleep-as-android")
GDPR_CLOUD_DIR = Path("/realm/data/exports/health/raw/samsung-gdpr-cloud")
PROCESSED = Path("/realm/data/exports/health/processed")

# Bump CSV field size limit for exercise/ECG rows with large embedded JSON
csv.field_size_limit(sys.maxsize)


# ── Parsing helpers ──────────────────────────────────────────────────────────


def read_samsung_csv(path: Path) -> list[dict]:
    """Samsung Health CSV: line 1 = metadata, line 2 = header, rest = data."""
    with open(path, encoding="utf-8-sig") as f:
        f.readline()  # skip metadata
        reader = csv.DictReader(f)
        return [row for row in reader if any(row.values())]


def read_samsung_csv_bytes(data: bytes) -> list[dict]:
    """Read Samsung Health CSV from in-memory bytes (for archive extraction)."""
    text = data.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    # Skip metadata line
    remaining = "".join(lines[1:])
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
        with open(csv_file, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if any(row.values()):
                    all_rows.append(row)

    # Deduplicate by datauuid if the column exists
    if all_rows and "datauuid" in all_rows[0]:
        seen: dict[str, dict] = {}
        for row in all_rows:
            uuid = row.get("datauuid", "")
            if uuid:
                existing = seen.get(uuid)
                if existing is None or _non_empty_count(row) > _non_empty_count(
                    existing
                ):
                    seen[uuid] = row
            else:
                seen[id(row)] = row  # type: ignore[arg-type]
        return list(seen.values())

    return all_rows


def _gdpr_csv_sort_key(path: Path) -> tuple[int, str]:
    """Sort key: base file first (index 0), then by numeric suffix."""
    name = path.stem
    m = re.search(r"\((\d+)\)$", name)
    if m:
        return (int(m.group(1)), name)
    return (-1, name)  # base file sorts first


def _non_empty_count(row: dict) -> int:
    """Count non-empty, non-None values in a row."""
    return sum(1 for v in row.values() if v is not None and v != "")


def parse_offset(offset_str: str) -> float:
    """Parse 'UTC+0200' -> 2.0 hours."""
    if not offset_str or not offset_str.startswith("UTC"):
        return 0.0
    sign = 1 if "+" in offset_str else -1
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
    if v is None or v == "" or str(v).lower() == "nan":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def try_int(v: str | None) -> int | None:
    if v is None or v == "" or str(v).lower() == "nan":
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
    return sorted(
        d for d in HEALTH_RAW.iterdir() if d.is_dir() and list(d.glob("*.csv"))
    )


def extract_csv_from_zip(zip_path: Path, pattern: str) -> list[dict]:
    """Extract and parse a Samsung CSV matching pattern from a zip archive."""
    rows: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if pattern in name and name.endswith(".csv"):
                    data = zf.read(name)
                    rows.extend(read_samsung_csv_bytes(data))
    except (zipfile.BadZipFile, KeyError):
        pass
    return rows


def extract_csv_from_tar(tar_path: Path, pattern: str) -> list[dict]:
    """Extract and parse a Samsung CSV matching pattern from a tar(.gz) archive."""
    rows: list[dict] = []
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf.getmembers():
                if pattern in member.name and member.name.endswith(".csv"):
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


def merge_by_datauuid(
    base: dict[str, dict], incoming: dict[str, dict]
) -> dict[str, dict]:
    """Merge incoming records into base, preferring the record with more populated fields."""
    merged = dict(base)
    for uuid, rec in incoming.items():
        existing = merged.get(uuid)
        if existing is None:
            merged[uuid] = rec
        else:
            # Keep the record with more non-None/non-empty values
            existing_count = sum(
                1 for v in existing.values() if v is not None and v != "" and v != 0
            )
            incoming_count = sum(
                1 for v in rec.values() if v is not None and v != "" and v != 0
            )
            if incoming_count > existing_count:
                merged[uuid] = rec
    return merged


def write_jsonl(records: list[dict], filename: str, label: str, dry_run: bool) -> int:
    """Write records to JSONL file, or report dry-run count."""
    if dry_run:
        print(f"[dry-run] Would write {len(records)} {label} records")
        return len(records)
    out = PROCESSED / filename
    with open(out, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    _write_product_manifest(out, records, label)
    print(f"{label}: {len(records)} records -> {out}")
    return len(records)


def _write_product_manifest(path: Path, records: list[dict[str, Any]], label: str) -> None:
    first, last = _record_date_bounds(records)
    manifest = {
        "dataset": f"health.{path.stem}",
        "label": label,
        "path": str(path),
        "row_count": len(records),
        "first_date": first,
        "last_date": last,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record_date_bounds(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    first: str | None = None
    last: str | None = None
    for record in records:
        stamp = _record_date(record)
        if stamp is None:
            continue
        first = stamp if first is None or stamp < first else first
        last = stamp if last is None or stamp > last else last
    return first, last


def _record_date(record: dict[str, Any]) -> str | None:
    for key in (
        "date",
        "day",
        "start_time",
        "timestamp",
        "created_at",
        "time",
        "start_local",
        "end_local",
        "end_time",
    ):
        value = record.get(key)
        if not isinstance(value, str) or not value:
            continue
        raw = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).date().isoformat()
        except ValueError:
            continue
    return None


__all__ = [
    "GDPR_CLOUD_DIR",
    "HEALTH_RAW",
    "PROCESSED",
    "SAA_RAW",
    "extract_csv_from_tar",
    "extract_csv_from_zip",
    "iter_archive_csv_rows",
    "iter_export_dirs",
    "merge_by_datauuid",
    "parse_dt",
    "parse_gdpr_dt",
    "parse_gdpr_offset",
    "parse_offset",
    "read_gdpr_cloud_csvs",
    "read_samsung_csv",
    "read_samsung_csv_bytes",
    "try_float",
    "try_int",
    "try_json",
    "write_jsonl",
]
