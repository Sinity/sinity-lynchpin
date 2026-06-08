"""Webhistory ingest pipeline — extract, dedup, merge.

Reads browser data from new sources (browser SQLite DBs, Takeout Chrome History
JSONs), normalizes to raw NDJSON, deduplicates against existing canonical
segments, and regenerates the merged ``full_history.ndjson``.

After this pipeline runs, the read-only ``lynchpin.sources.web`` functions
(``daily_browsing``, ``iter_gestalt_events``, etc.) naturally pick up the new
data from the canonical paths.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import logical_date
from ..sources.browser_db import iter_browser_db_visits
from ..sources.google_takeout import (
    discover_takeout_archives,
    iter_chrome_history_batches,
)
from ..sources.web import (
    WebHistoryVisit,
    iter_gestalt_events,
    iter_file_visits,
    payload_timestamp,
    normalize_url,
)
from .manifest_windows import merge_manifest_covered_dates

logger = logging.getLogger(__name__)

# Default tolerance for dedup: ±30s catches Chrome recording the same
# page view multiple times (initial typed URL + redirect chain entries).
DEFAULT_DEDUP_TOLERANCE_S = 30
WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION = 1
WebHistoryRow = tuple[datetime, str, str, str]
_DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})")

# Canonical historical browser snapshots. Disk-image recovery directories are
# provenance only; ingestion reads from the webhistory ontology.
_HISTORICAL_BROWSER_DB_ROOTS: tuple[Path, ...] = (
    Path("/realm/data/captures/webhistory/browser-dbs/historical/windows_install_ezode"),
    Path("/realm/data/captures/webhistory/browser-dbs/historical/jbr_vhdx_michab"),
)

# ── extraction ────────────────────────────────────────────────────────


def _discover_browser_dbs() -> list[tuple[Path, str, str]]:
    """Return [(path, kind, label), ...] for all known browser SQLite DBs."""
    # Non-history DBs that share the "chrome"/"vivaldi" name prefix
    _NON_HISTORY = {"chrome_webdata.db", "vivaldi_webdata.db", "vivaldi_logindata.db",
                    "vivaldi_topsites.db", "vivaldi_favicons.db", "edge_webdata.db"}
    dbs: list[tuple[Path, str, str]] = []
    for root in _HISTORICAL_BROWSER_DB_ROOTS:
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if not p.suffix == ".db":
                continue
            if p.name.lower() in _NON_HISTORY:
                continue
            name = p.name.lower()
            if "firefox" in name or name.startswith("ff_"):
                dbs.append((p, "firefox", f"browser_db:{p.name}"))
            elif any(k in name for k in ("chrome", "edge", "vivaldi", "chromium")):
                dbs.append((p, "chromium", f"browser_db:{p.name}"))
    return dbs


def _discover_takeout_chrome_archives() -> list[Path]:
    return list(discover_takeout_archives())


def _discover_manual_history_exports() -> list[Path]:
    """Return ad hoc browser history exports staged in the user's download dirs."""
    candidates: list[Path] = []
    for root in (
        Path.home() / "Downloads",
        Path.home() / "downloads",
        Path.home() / "Download",
        Path.home() / "download",
    ):
        if not root.is_dir():
            continue
        for name in ("history.json", "history.csv"):
            path = root / name
            if path.is_file():
                candidates.append(path)
    return candidates


def extract_browser_data(
    *,
    raw_dir: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Extract visits from all new browser sources, write raw NDJSON to *raw_dir*.

    Returns a list of report dicts describing each extracted source.
    """
    cfg = get_config()
    raw_dir = raw_dir or cfg.webhistory_raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []

    # Browser SQLite DBs
    for path, kind, label in _discover_browser_dbs():
        visits = list(iter_browser_db_visits(path, kind=kind, source_label=label))
        report = _write_raw_batch(raw_dir, path.name, visits, dry_run=dry_run)
        report["kind"] = kind
        reports.append(report)

    for batch in iter_chrome_history_batches():
        report = _write_raw_batch(
            raw_dir,
            f"{batch.archive.stem}_{Path(batch.member).name}",
            list(batch.visits),
            dry_run=dry_run,
        )
        report["kind"] = "takeout_chrome_archive"
        report["archive"] = str(batch.archive)
        report["member"] = batch.member
        reports.append(report)

    for path in _discover_manual_history_exports():
        visits = list(iter_file_visits(path))
        report = _write_raw_batch(raw_dir, f"manual_{path.name}", visits, dry_run=dry_run)
        report["kind"] = "manual_history_export"
        report["source_path"] = str(path)
        reports.append(report)

    return reports


def _write_raw_batch(
    raw_dir: Path,
    source_name: str,
    visits: list[WebHistoryVisit],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write a batch of WebHistoryVisit objects as NDJSON to raw_dir."""
    if not visits:
        return {"source": source_name, "visits": 0, "path": None, "written": False}

    visits.sort(key=lambda v: v.timestamp)
    bounds = _visit_date_bounds([v.timestamp for v in visits])
    start = bounds["first_date"]
    end = bounds["last_date"]
    stem = source_name.rsplit(".", 1)[0]
    out_path = raw_dir / f"{stem}_{start}_to_{end}.ndjson"

    if not dry_run:
        with out_path.open("w", encoding="utf-8") as fh:
            for v in visits:
                fh.write(
                    json.dumps(
                        {
                            "iso_time": v.timestamp.isoformat(),
                            "url": v.url,
                            "title": v.title,
                            "source": v.source,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    return {
        "source": source_name,
        "visits": len(visits),
        "path": str(out_path),
        "start": start,
        "end": end,
        **bounds,
        "written": not dry_run,
    }


# ── dedup ─────────────────────────────────────────────────────────────


def _make_dedup_key(norm_url: str, ts: datetime) -> tuple[str, datetime]:
    """Canonical dedup key: (normalized URL, timestamp rounded to second)."""
    return (norm_url, ts.replace(microsecond=0))


def dedup_raw_files(
    *,
    raw_dir: Path | None = None,
    data_dir: Path | None = None,
    tolerance_seconds: int = DEFAULT_DEDUP_TOLERANCE_S,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Dedup raw NDJSON files against existing canonical segments.

    For each raw file not yet represented in the canonical data_dir, deduplicates
    its events against the global seen-set and writes a ``_unique_`` segment.

    Returns a report list.
    """
    cfg = get_config()
    raw_dir = raw_dir or cfg.webhistory_raw_dir
    data_dir = data_dir or cfg.webhistory_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    # Build seen-set from existing canonical segments
    seen: dict[tuple[str, datetime], bool] = {}
    if data_dir.is_dir():
        for visit in iter_gestalt_events(data_dir):
            key = _make_dedup_key(normalize_url(visit.url), visit.timestamp)
            seen[key] = True

    reports: list[dict[str, Any]] = []
    if not raw_dir.is_dir():
        return reports

    for raw_path in sorted(raw_dir.iterdir()):
        if raw_path.suffix not in (".json", ".csv", ".ndjson", ".jsonl"):
            continue
        if "_unique_" in raw_path.name:
            continue

        logger.debug("dedup: %s", raw_path.name)
        visits = list(iter_file_visits(raw_path))

        if not visits:
            continue

        unique: list[WebHistoryVisit] = []
        duplicates = 0
        for v in visits:
            norm = normalize_url(v.url)
            base = v.timestamp.replace(microsecond=0)
            is_dup = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    duplicates += 1
                    is_dup = True
                    break
            if is_dup:
                continue
            seen[(norm, base)] = True
            unique.append(v)

        if not unique:
            reports.append({
                "file": str(raw_path),
                "unique": 0,
                "duplicates": duplicates,
                "kept_path": None,
            })
            continue

        unique.sort(key=lambda v: v.timestamp)
        bounds = _visit_date_bounds([v.timestamp for v in unique])
        start = bounds["first_date"]
        end = bounds["last_date"]
        stem = raw_path.stem.rsplit(".", 1)[0]
        out_path = data_dir / f"{stem}_unique_{start}_to_{end}.ndjson"

        if not dry_run:
            with out_path.open("w", encoding="utf-8") as fh:
                for v in unique:
                    fh.write(
                        json.dumps(
                            {
                                "iso_time": v.timestamp.isoformat(),
                                "url": v.url,
                                "title": v.title,
                                "source": v.source,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        reports.append({
            "file": str(raw_path),
            "unique": len(unique),
            "duplicates": duplicates,
            "kept_path": str(out_path),
            "start": start,
            "end": end,
            **bounds,
        })

    return reports


# ── merge ─────────────────────────────────────────────────────────────


def build_full_history(
    *,
    data_dir: Path | None = None,
    output: Path | None = None,
    tolerance_seconds: int = DEFAULT_DEDUP_TOLERANCE_S,
    dry_run: bool = False,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Merge all canonical (``_unique_``) segments into ``full_history.ndjson``.

    Re-applies the same dedup logic cross-segment so that overlapping exports
    don't produce duplicate entries in the merged output.
    """
    if (start is None) != (end is None):
        raise MaterializationError("webhistory", reason="webhistory materialization requires both start and end")
    if start is not None and end is not None and end <= start:
        raise MaterializationError("webhistory", reason="webhistory materialization end must be after start")
    cfg = get_config()
    data_dir = data_dir or cfg.webhistory_dir
    output = output or cfg.webhistory_ndjson or Path(str(cfg.webhistory_raw_dir).replace("/raw", "/derived")) / "full_history.ndjson"

    if not data_dir.is_dir():
        return {"output": str(output), "row_count": 0, "duplicate_count": 0, "skipped": True}
    input_files = tuple(_candidate_segment_files(data_dir, start=start, end=end))

    segment_visits = _load_segment_visits(input_files, start=start, end=end)
    if start is not None and end is not None:
        visits = [
            row
            for row in _load_existing_full_history(output)
            if not (start <= logical_date(row[0]) < end)
        ]
        visits.extend(segment_visits)
        covered_dates = _merge_covered_dates(
            manifest=full_history_manifest_path(output),
            rows=visits,
            start=start,
            end=end,
        )
    else:
        visits = segment_visits
        covered_dates = tuple(sorted({logical_date(timestamp) for timestamp, _url, _title, _source in visits}))

    visits.sort(key=lambda item: item[0])

    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)

    seen: dict[tuple[str, datetime], bool] = {}
    row_count = 0
    duplicate_count = 0
    output_source_counts: dict[str, int] = {}

    handle = None if dry_run else output.open("w", encoding="utf-8")
    try:
        for timestamp, url, title, source in visits:
            norm = normalize_url(url)
            base = timestamp.replace(microsecond=0)
            is_dup = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    is_dup = True
                    duplicate_count += 1
                    break
            if is_dup:
                continue
            seen[(norm, base)] = True
            if handle is not None:
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
            output_source_counts[source] = output_source_counts.get(source, 0) + 1
    finally:
        if handle is not None:
            handle.close()

    report = {
        "output": str(output),
        "input_visits": len(segment_visits),
        "row_count": row_count,
        "duplicate_count": duplicate_count,
        "dry_run": dry_run,
        "dedup_tolerance_seconds": tolerance_seconds,
        "segments": _segment_inventory_from_visits(segment_visits),
        "source_counts": dict(sorted(output_source_counts.items())),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive" if start is not None and end is not None else None,
    }
    input_source_counts = {
        row["path"]: int(row["input_visit_count"])
        for row in report["segments"]
        if isinstance(row.get("path"), str)
    }
    report["input_source_counts"] = dict(sorted(input_source_counts.items()))
    report["source_duplicate_counts"] = {
        source: max(count - output_source_counts.get(source, 0), 0)
        for source, count in sorted(input_source_counts.items())
    }
    if covered_dates:
        report["first_date"] = covered_dates[0].isoformat()
        report["last_date"] = covered_dates[-1].isoformat()
        report["date_boundary"] = "logical_06:00_local"
    if visits:
        report["first_visit_at"] = visits[0][0].isoformat()
        report["last_visit_at"] = visits[-1][0].isoformat()
        report.update(
            {
                key: value
                for key, value in _visit_date_bounds([timestamp for timestamp, _url, _title, _source in visits]).items()
                if key not in {"first_date", "last_date"}
            }
        )
    report["segment_count"] = len(input_files)
    report["input_files"] = [str(path) for path in input_files]
    report["input_file_count"] = len(input_files)
    report["input_latest_mtime"] = latest_mtime_iso(input_files)

    if not dry_run:
        _write_full_history_manifest(output, report)

    return report


def _load_segment_visits(
    paths: tuple[Path, ...],
    *,
    start: date | None,
    end: date | None,
) -> list[WebHistoryRow]:
    visits: list[WebHistoryRow] = []
    for path in paths:
        for visit in iter_file_visits(path):
            day = logical_date(visit.timestamp)
            if start is not None and end is not None and not (start <= day < end):
                continue
            visits.append((visit.timestamp, visit.url, visit.title, visit.source))
    return visits


def _load_existing_full_history(path: Path) -> list[WebHistoryRow]:
    if not path.exists():
        return []
    rows: list[WebHistoryRow] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            timestamp = payload_timestamp(payload)
            if timestamp is None:
                continue
            rows.append(
                (
                    timestamp,
                    str(payload.get("url") or ""),
                    str(payload.get("title") or ""),
                    str(payload.get("source") or path),
                )
            )
    return rows


def _candidate_segment_files(data_dir: Path, *, start: date | None, end: date | None) -> list[Path]:
    files = _canonical_segment_files(data_dir)
    if start is None or end is None:
        return files
    candidates: list[Path] = []
    for path in files:
        bounds = _segment_file_bounds(path)
        if bounds is None:
            candidates.append(path)
            continue
        first, last = bounds
        if first < end and last + timedelta(days=1) > start:
            candidates.append(path)
    return candidates


def _segment_file_bounds(path: Path) -> tuple[date, date] | None:
    matches = _DATE_RANGE_RE.findall(path.stem)
    if not matches:
        return None
    first_raw, last_raw = matches[-1]
    try:
        return date.fromisoformat(first_raw), date.fromisoformat(last_raw)
    except ValueError:
        return None


def _merge_covered_dates(
    *,
    manifest: Path,
    rows: list[WebHistoryRow],
    start: date,
    end: date,
) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=manifest,
        observed_dates=(logical_date(timestamp) for timestamp, _url, _title, _source in rows),
        start=start,
        end=end,
    )


def _segment_inventory_from_visits(
    visits: list[tuple[datetime, str, str, str]],
) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for timestamp, _url, _title, source in visits:
        row = by_source.setdefault(
            source,
            {
                "path": source,
                "input_visit_count": 0,
                "first_visit_at": timestamp.isoformat(),
                "last_visit_at": timestamp.isoformat(),
            },
        )
        row["input_visit_count"] += 1
        if timestamp.isoformat() < row["first_visit_at"]:
            row["first_visit_at"] = timestamp.isoformat()
        if timestamp.isoformat() > row["last_visit_at"]:
            row["last_visit_at"] = timestamp.isoformat()
    return [by_source[source] for source in sorted(by_source)]


def _canonical_segment_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return [
        *sorted(data_dir.glob("*.jsonl")),
        *sorted(data_dir.glob("*.ndjson")),
        *sorted(data_dir.glob("*.json")),
        *sorted(data_dir.glob("*.csv")),
    ]


def full_history_manifest_path(output: Path | None = None) -> Path:
    if output is not None:
        target = output
    else:
        cfg = get_config()
        target = cfg.webhistory_ndjson or Path(str(cfg.webhistory_raw_dir).replace("/raw", "/derived")) / "full_history.ndjson"
    return target.with_name(f"{target.stem}.manifest.json")


def _write_full_history_manifest(output: Path, report: dict[str, Any]) -> None:
    manifest = {
        "dataset": "webhistory.full_history",
        "schema_version": WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
        "materialized_path": str(output),
        "materialized_at": datetime.now().astimezone().isoformat(),
        **report,
    }
    full_history_manifest_path(output).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _visit_date_bounds(timestamps: list[datetime]) -> dict[str, str]:
    """Return logical coverage bounds plus raw timestamp-date diagnostics."""
    logical_dates = [logical_date(timestamp) for timestamp in timestamps]
    return {
        "first_date": min(logical_dates).isoformat(),
        "last_date": max(logical_dates).isoformat(),
        "first_timestamp_date": min(timestamps).date().isoformat(),
        "last_timestamp_date": max(timestamps).date().isoformat(),
        "date_boundary": "logical_06:00_local",
    }


# ── orchestration ─────────────────────────────────────────────────────


def run(
    *,
    raw_dir: Path | None = None,
    data_dir: Path | None = None,
    output: Path | None = None,
    tolerance_seconds: int = DEFAULT_DEDUP_TOLERANCE_S,
    dry_run: bool = False,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Full pipeline: extract → dedup → merge.

    Returns a summary dict suitable for JSON serialization.
    """
    # 1. Extract new browser data → raw NDJSON
    extract_reports = extract_browser_data(raw_dir=raw_dir, dry_run=dry_run)
    total_extracted = sum(r["visits"] for r in extract_reports)

    # 2. Dedup → canonical segments
    dedup_reports = dedup_raw_files(
        raw_dir=raw_dir, data_dir=data_dir,
        tolerance_seconds=tolerance_seconds, dry_run=dry_run,
    )
    total_unique = sum(r["unique"] for r in dedup_reports)

    # 3. Regenerate merged full_history.ndjson
    merge_report = build_full_history(
        data_dir=data_dir, output=output,
        tolerance_seconds=tolerance_seconds, dry_run=dry_run,
        start=start,
        end=end,
    )

    return {
        "extract": {
            "sources": len(extract_reports),
            "total_visits": total_extracted,
            "details": extract_reports,
        },
        "dedup": {
            "files_processed": len(dedup_reports),
            "total_unique": total_unique,
            "details": dedup_reports,
        },
        "merge": merge_report,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical webhistory products")
    parser.add_argument("--dry-run", action="store_true", help="report work without writing outputs")
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--tolerance-seconds", type=int, default=DEFAULT_DEDUP_TOLERANCE_S)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    report = run(
        raw_dir=args.raw_dir,
        data_dir=args.data_dir,
        output=args.output,
        tolerance_seconds=args.tolerance_seconds,
        dry_run=args.dry_run,
        start=args.start,
        end=args.end,
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
