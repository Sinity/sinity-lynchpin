"""Webhistory deduplication and merged-history materialization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from ..core.cache import file_digest
from ..core.io import write_text_if_changed
from ..sources.captures import webhistory_raw
from ..sources.captures.webhistory import iter_gestalt_events, normalize_url


def dedup_webhistory(
    *,
    raw_root: Path,
    output_dir: Path,
    tolerance_seconds: int,
    files: list[str] | None = None,
    report_path: Path,
    manifest_path: Path,
    force: bool = False,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = webhistory_raw.raw_files(raw_root, files or None)
    if not paths:
        return {
            "missing_inputs": True,
            "skipped": False,
            "report_rows": [],
            "report_path": report_path,
            "manifest_path": manifest_path,
        }

    signatures = [file_digest(path) for path in paths]
    if not force and _manifest_matches(manifest_path, raw_root, signatures, tolerance_seconds):
        return {
            "missing_inputs": False,
            "skipped": True,
            "report_rows": [],
            "report_path": report_path,
            "manifest_path": manifest_path,
        }

    seen: Dict[Tuple[str, datetime], bool] = {}
    report_rows: List[Dict[str, object]] = []

    for path, signature in zip(paths, signatures):
        entries = webhistory_raw.load_raw_file(path, signature)
        unique = []
        duplicates = 0
        for entry in entries:
            dt = entry.timestamp.astimezone(timezone.utc)
            norm = normalize_url(entry.url)
            base = dt.replace(microsecond=0)
            is_duplicate = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    duplicates += 1
                    is_duplicate = True
                    break
            if is_duplicate:
                continue
            seen[(norm, base)] = True
            unique.append(entry)

        if unique:
            unique.sort(key=lambda item: item.timestamp)
            start = unique[0].timestamp.date().isoformat()
            end = unique[-1].timestamp.date().isoformat()
            out_path = output_dir / f"{path.stem}_unique_{start}_to_{end}{path.suffix}"
            _write_dedup_output(out_path, path, unique)
            report_rows.append(
                {
                    "file": str(path),
                    "unique": len(unique),
                    "duplicates": duplicates,
                    "kept_path": str(out_path),
                    "start": start,
                    "end": end,
                }
            )
            continue

        report_rows.append(
            {
                "file": str(path),
                "unique": 0,
                "duplicates": duplicates,
                "kept_path": None,
                "start": None,
                "end": None,
            }
        )

    write_text_if_changed(report_path, json.dumps(report_rows, ensure_ascii=False, indent=2))
    write_text_if_changed(
        manifest_path,
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "raw_root": str(raw_root),
                "tolerance_seconds": tolerance_seconds,
                "files": [list(sig) for sig in signatures],
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    return {
        "missing_inputs": False,
        "skipped": False,
        "report_rows": report_rows,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


def build_full_history(*, root: Path, output: Path, tolerance_seconds: int) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)

    visits: list[tuple[datetime, str, str, str]] = []
    for visit in iter_gestalt_events(root):
        visits.append((visit.timestamp, visit.url, visit.title, Path(visit.source).name))

    visits.sort(key=lambda item: item[0])

    seen: Dict[Tuple[str, datetime], bool] = {}
    row_count = 0
    duplicate_count = 0
    with output.open("w", encoding="utf-8") as handle:
        for timestamp, url, title, source in visits:
            norm = normalize_url(url)
            base = timestamp.replace(microsecond=0)
            is_duplicate = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    is_duplicate = True
                    duplicate_count += 1
                    break
            if is_duplicate:
                continue
            seen[(norm, base)] = True
            handle.write(
                json.dumps(
                    {
                        "url": url,
                        "title": title,
                        "norm": norm,
                        "source": source,
                        "iso_time": timestamp.isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            row_count += 1

    return {
        "output": output,
        "row_count": row_count,
        "duplicate_count": duplicate_count,
    }


def _write_dedup_output(
    out_path: Path,
    source: Path,
    entries: Iterable[webhistory_raw.WebHistoryRawEntry],
) -> None:
    suffix = out_path.suffix.lower()
    if suffix in {".json", ".ndjson", ".jsonl"}:
        with out_path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                payload = entry.payload()
                payload["_source_file"] = source.name
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    if suffix == ".csv":
        entry_rows = list(entries)
        if not entry_rows:
            return
        fieldnames = list(entry_rows[0].payload().keys())
        if "_source_file" not in fieldnames:
            fieldnames.append("_source_file")
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            writer = _csv_writer(handle, fieldnames)
            writer.writeheader()
            for entry in entry_rows:
                row = entry.payload()
                row["_source_file"] = source.name
                writer.writerow(row)
        return
    raise ValueError(f"Unsupported output format: {out_path}")


def _csv_writer(handle, fieldnames):
    import csv

    return csv.DictWriter(handle, fieldnames=fieldnames)


def _manifest_matches(
    manifest_path: Path,
    raw_root: Path,
    signatures: List[Tuple[str, int | None, int | None, str | None]],
    tolerance_seconds: int,
) -> bool:
    if not manifest_path.exists():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if payload.get("raw_root") != str(raw_root):
        return False
    if payload.get("tolerance_seconds") != tolerance_seconds:
        return False
    return payload.get("files") == [list(sig) for sig in signatures]
