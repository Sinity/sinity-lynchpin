"""Webhistory ingest pipeline — extract, dedup, merge.

Reads browser data from new sources (browser SQLite DBs, Takeout Chrome History
JSONs), normalizes to raw NDJSON, deduplicates against existing canonical
segments, and regenerates the merged ``full_history.ndjson``.

After this pipeline runs, the read-only ``lynchpin.sources.web`` functions
(``daily_browsing``, ``iter_gestalt_events``, etc.) naturally pick up the new
data from the canonical paths.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.cache import file_digest, write_text_if_changed
from ..core.config import get_config
from ..sources.browser_db import BROWSER_DB_KINDS, iter_browser_db_visits
from ..sources.takeout_chrome import iter_takeout_chrome_visits
from ..sources.web import (
    WebHistoryVisit,
    iter_gestalt_events,
    iter_ndjson_events,
    normalize_url,
)

logger = logging.getLogger(__name__)

# Default tolerance for dedup: ±30s catches Chrome recording the same
# page view multiple times (initial typed URL + redirect chain entries).
DEFAULT_DEDUP_TOLERANCE_S = 30

# Hard-coded paths for machine_imgs browser data — these are snapshot paths,
# not configurable live directories.
_MACHINE_IMG_BROWSER_DB_ROOTS: tuple[Path, ...] = (
    Path("/realm/data/exports/machine_imgs/windows_install_ezode/browsers"),
    Path("/realm/data/exports/machine_imgs/jbr_vhdx_michab/browsers"),
)

_MACHINE_IMG_TAKEOUT_CHROME_ROOTS: tuple[Path, ...] = (
    Path("/realm/data/exports/machine_imgs/google_takeout_extracted"),
)


# ── extraction ────────────────────────────────────────────────────────


def _discover_browser_dbs() -> list[tuple[Path, str, str]]:
    """Return [(path, kind, label), ...] for all known browser SQLite DBs."""
    # Non-history DBs that share the "chrome"/"vivaldi" name prefix
    _NON_HISTORY = {"chrome_webdata.db", "vivaldi_webdata.db", "vivaldi_logindata.db",
                    "vivaldi_topsites.db", "vivaldi_favicons.db", "edge_webdata.db"}
    dbs: list[tuple[Path, str, str]] = []
    for root in _MACHINE_IMG_BROWSER_DB_ROOTS:
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


def _discover_takeout_chrome_jsons() -> list[tuple[Path, str]]:
    """Return [(path, label), ...] for all known Takeout Chrome History JSONs."""
    files: list[tuple[Path, str]] = []
    for root in _MACHINE_IMG_TAKEOUT_CHROME_ROOTS:
        if not root.is_dir():
            continue
        for takeout_dir in root.iterdir():
            if not takeout_dir.is_dir():
                continue
            for name in ("BrowserHistory.json", "History.json"):
                candidate = takeout_dir / "Takeout" / "Chrome" / name
                if candidate.is_file() and candidate.stat().st_size > 100:
                    files.append((candidate, f"takeout_chrome:{candidate.parent.parent.parent.name}"))
    return files


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

    # Takeout Chrome History JSONs
    for path, label in _discover_takeout_chrome_jsons():
        visits = list(iter_takeout_chrome_visits(path, source_label=label))
        report = _write_raw_batch(raw_dir, path.name, visits, dry_run=dry_run)
        report["kind"] = "takeout_chrome"
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
    start = visits[0].timestamp.date().isoformat()
    end = visits[-1].timestamp.date().isoformat()
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
        if not raw_path.suffix in (".json", ".csv", ".ndjson", ".jsonl"):
            continue
        if "_unique_" in raw_path.name:
            continue

        logger.debug("dedup: %s", raw_path.name)
        visits = list(iter_ndjson_events(raw_path))

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
        start = unique[0].timestamp.date().isoformat()
        end = unique[-1].timestamp.date().isoformat()
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
        })

    return reports


# ── merge ─────────────────────────────────────────────────────────────


def build_full_history(
    *,
    data_dir: Path | None = None,
    output: Path | None = None,
    tolerance_seconds: int = DEFAULT_DEDUP_TOLERANCE_S,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge all canonical (``_unique_``) segments into ``full_history.ndjson``.

    Re-applies the same dedup logic cross-segment so that overlapping exports
    don't produce duplicate entries in the merged output.
    """
    cfg = get_config()
    data_dir = data_dir or cfg.webhistory_dir
    output = output or Path(str(cfg.webhistory_raw_dir).replace("/raw", "/derived")) / "full_history.ndjson"

    if not data_dir.is_dir():
        return {"output": str(output), "row_count": 0, "duplicate_count": 0, "skipped": True}

    # Collect and sort all visits from canonical segments
    visits: list[tuple[datetime, str, str, str]] = []
    for v in iter_gestalt_events(data_dir):
        visits.append((v.timestamp, v.url, v.title, v.source))

    visits.sort(key=lambda item: item[0])

    if dry_run:
        return {
            "output": str(output),
            "input_visits": len(visits),
            "dry_run": True,
        }

    output.parent.mkdir(parents=True, exist_ok=True)

    seen: dict[tuple[str, datetime], bool] = {}
    row_count = 0
    duplicate_count = 0

    with output.open("w", encoding="utf-8") as fh:
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
            fh.write(
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
        "output": str(output),
        "row_count": row_count,
        "duplicate_count": duplicate_count,
    }


# ── orchestration ─────────────────────────────────────────────────────


def run(
    *,
    raw_dir: Path | None = None,
    data_dir: Path | None = None,
    output: Path | None = None,
    tolerance_seconds: int = DEFAULT_DEDUP_TOLERANCE_S,
    dry_run: bool = False,
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
