"""Derived webhistory artefacts.

Builds deduped canonical segments and merged NDJSON timelines from raw exports.

CLI Usage:
    python -m lynchpin.ingest.webhistory dedup
    python -m lynchpin.ingest.webhistory compare
    python -m lynchpin.ingest.webhistory full-history
"""

from __future__ import annotations

from collections import Counter
import json
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import typer

from ..core.cache import file_digest
from ..core.config import get_config
from ..core.io import write_text_if_changed
from ..sources.captures import webhistory_raw
from ..sources.captures.webhistory import WebHistoryVisit, iter_gestalt_events, iter_ndjson_events, normalize_url

app = typer.Typer(help="Webhistory derived artefacts")


@app.command()
def dedup(
    raw_root: Optional[Path] = typer.Option(
        None,
        "--raw-root",
        help="Raw webhistory export directory (defaults to lynchpin config).",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Directory to write deduped segments (defaults to the canonical webhistory dir).",
    ),
    tolerance_seconds: int = typer.Option(
        30,
        "--tolerance-seconds",
        help="Timestamp tolerance (seconds) when identifying duplicates. Default 30s catches Chrome's habit of recording the same page view multiple times within seconds.",
    ),
    files: List[str] = typer.Option(
        [],
        "--file",
        help="Specific raw filenames to process (repeatable, relative to --raw-root).",
    ),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        help="Optional JSON report path (defaults to the canonical webhistory derived directory).",
    ),
    manifest: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help="Optional manifest path to record input signatures (defaults to the canonical webhistory derived directory).",
    ),
    force: bool = typer.Option(False, "--force", help="Rebuild even if manifest matches."),
) -> None:
    """Sequentially deduplicate raw exports into canonical segments."""
    cfg = get_config()
    resolved_raw = raw_root or cfg.webhistory_raw_dir
    resolved_output = output_dir or cfg.webhistory_dir
    resolved_output.mkdir(parents=True, exist_ok=True)
    derived_dir = resolved_output.parent / "derived"
    report_path = report or (derived_dir / "dedup_report.json")
    manifest_path = manifest or (derived_dir / "dedup_manifest.json")

    paths = webhistory_raw.raw_files(resolved_raw, files or None)
    if not paths:
        typer.secho(f"No raw webhistory files found in {resolved_raw}", fg=typer.colors.YELLOW)
        return

    signatures = [file_digest(path) for path in paths]
    if not force and _manifest_matches(manifest_path, resolved_raw, signatures, tolerance_seconds):
        typer.secho("✓ Dedup inputs unchanged; skipping.", fg=typer.colors.GREEN)
        return

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
            is_dup = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    duplicates += 1
                    is_dup = True
                    break
            if is_dup:
                continue
            key = (norm, base)
            seen[key] = True
            unique.append(entry)

        if unique:
            unique.sort(key=lambda item: item.timestamp)
            start = unique[0].timestamp.date().isoformat()
            end = unique[-1].timestamp.date().isoformat()
            out_path = resolved_output / f"{path.stem}_unique_{start}_to_{end}{path.suffix}"
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
            typer.secho(
                f"[keep] {path.name} → {out_path.name} ({len(unique)} unique, {duplicates} dup)",
                fg=typer.colors.GREEN,
            )
        else:
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
            typer.secho(f"[drop] {path.name} (all duplicates)", fg=typer.colors.YELLOW)

    report_text = json.dumps(report_rows, ensure_ascii=False, indent=2)
    write_text_if_changed(report_path, report_text)

    manifest_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_root": str(resolved_raw),
        "tolerance_seconds": tolerance_seconds,
        "files": [list(sig) for sig in signatures],
        "report": str(report_path),
    }
    manifest_text = json.dumps(manifest_payload, ensure_ascii=False, indent=2)
    write_text_if_changed(manifest_path, manifest_text)


@app.command()
def compare(
    canonical: Optional[Path] = typer.Option(
        None,
        "--canonical",
        help="Canonical gestalt directory or NDJSON file (defaults to lynchpin config).",
    ),
    candidate: Optional[Path] = typer.Option(
        None,
        "--candidate",
        help="Candidate dataset (gestalt dir or NDJSON file) to compare against canonical.",
    ),
    tolerance_seconds: int = typer.Option(
        5,
        "--tolerance-seconds",
        help="Timestamp tolerance when matching events.",
    ),
    sample: int = typer.Option(20, "--sample", help="Max samples to include for missing/extra lists."),
    output: Optional[Path] = typer.Option(
        Path("artefacts/webhistory/gestalt_compare.json"),
        "--output",
        help="Optional JSON output path.",
    ),
) -> None:
    """Compare canonical vs candidate datasets with URL-normalized matching."""
    cfg = get_config()
    resolved_canonical = canonical or cfg.webhistory_dir
    resolved_candidate = candidate or cfg.webhistory_ndjson or cfg.webhistory_dir
    report = _compare_gestalt(resolved_canonical, resolved_candidate, tolerance_seconds, sample)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        write_text_if_changed(output, payload)
    typer.echo(payload)


@app.command()
def full_history(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Canonical gestalt segments directory (defaults to lynchpin config).",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Output NDJSON path (defaults to the canonical webhistory derived directory).",
    ),
    tolerance_seconds: int = typer.Option(
        30,
        "--tolerance-seconds",
        help="Cross-file dedup tolerance in seconds (URL + timestamp window). Default 30s catches cross-export duplicates where the same visit was recorded with slightly different timestamps.",
    ),
) -> None:
    """Write merged, deduplicated, chronologically-sorted NDJSON from canonical segments."""
    cfg = get_config()
    resolved_root = root or cfg.webhistory_dir
    derived_dir = cfg.webhistory_dir.parent / "derived"
    resolved_output = output or (derived_dir / "full_history.ndjson")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # Collect all visits, then sort + dedup across files
    visits: list[tuple[datetime, str, str, str]] = []
    for visit in iter_gestalt_events(resolved_root):
        visits.append((visit.timestamp, visit.url, visit.title, Path(visit.source).name))

    visits.sort(key=lambda v: v[0])

    seen: Dict[Tuple[str, datetime], bool] = {}
    count = 0
    dedup_count = 0
    with resolved_output.open("w", encoding="utf-8") as fh:
        for ts, url, title, source in visits:
            norm = normalize_url(url)
            base = ts.replace(microsecond=0)
            is_dup = False
            for delta in range(-tolerance_seconds, tolerance_seconds + 1):
                key = (norm, base + timedelta(seconds=delta))
                if key in seen:
                    is_dup = True
                    dedup_count += 1
                    break
            if is_dup:
                continue
            seen[(norm, base)] = True
            record = {
                "url": url,
                "title": title,
                "norm": norm,
                "source": source,
                "iso_time": ts.isoformat(),
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    typer.secho(
        f"✓ Wrote {count} webhistory rows → {resolved_output} ({dedup_count} cross-file duplicates removed)",
        fg=typer.colors.GREEN,
    )


@app.command()
def audit(
    raw_root: Optional[Path] = typer.Option(
        None,
        "--raw-root",
        help="Raw webhistory export directory (defaults to lynchpin config).",
    ),
    canonical: Optional[Path] = typer.Option(
        None,
        "--canonical",
        help="Canonical gestalt directory (defaults to lynchpin config).",
    ),
    merged: Optional[Path] = typer.Option(
        None,
        "--merged",
        help="Merged NDJSON file (defaults to the canonical derived full-history path).",
    ),
    tolerance_seconds: int = typer.Option(
        5,
        "--tolerance-seconds",
        help="Timestamp tolerance used by the dedup simulation.",
    ),
    sample: int = typer.Option(20, "--sample", help="Max samples to include for mismatch lists."),
    output: Optional[Path] = typer.Option(
        Path("artefacts/webhistory/gestalt_audit.json"),
        "--output",
        help="Optional JSON output path.",
    ),
) -> None:
    """Audit canonical outputs against raw inputs and merged NDJSON."""
    cfg = get_config()
    resolved_raw = raw_root or cfg.webhistory_raw_dir
    resolved_canonical = canonical or cfg.webhistory_dir
    resolved_merged = merged or cfg.webhistory_ndjson or (cfg.webhistory_dir.parent / "derived" / "full_history.ndjson")
    report = _audit_webhistory(
        raw_root=resolved_raw,
        canonical=resolved_canonical,
        merged=resolved_merged,
        tolerance=tolerance_seconds,
        sample=sample,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        write_text_if_changed(output, payload)
    typer.echo(payload)


def _write_dedup_output(
    out_path: Path, source: Path, entries: Iterable[webhistory_raw.WebHistoryRawEntry]
) -> None:
    suffix = out_path.suffix.lower()
    if suffix in {".json", ".ndjson", ".jsonl"}:
        with out_path.open("w", encoding="utf-8") as fh:
            for entry in entries:
                payload = entry.payload()
                payload["_source_file"] = source.name
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    if suffix == ".csv":
        entries = list(entries)
        if not entries:
            return
        fieldnames = list(entries[0].payload().keys())
        if "_source_file" not in fieldnames:
            fieldnames.append("_source_file")
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = _csv_writer(fh, fieldnames)
            writer.writeheader()
            for entry in entries:
                row = entry.payload()
                row["_source_file"] = source.name
                writer.writerow(row)
        return
    raise ValueError(f"Unsupported output format: {out_path}")


def _csv_writer(handle, fieldnames):
    import csv

    return csv.DictWriter(handle, fieldnames=fieldnames)


def _iter_events(path: Path) -> Iterator[WebHistoryVisit]:
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in {".ndjson", ".jsonl"}:
            yield from iter_ndjson_events(path)
            return
        raise ValueError(f"Unsupported file type: {path}")
    yield from iter_gestalt_events(path)


def _to_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _load_events(path: Path) -> List[Tuple[int, str, str]]:
    rows: List[Tuple[int, str, str]] = []
    for event in _iter_events(path):
        url = normalize_url(event.url or "")
        if not url:
            continue
        rows.append((_to_seconds(event.timestamp), url, event.source))
    return rows


def _load_event_key_data(path: Path) -> tuple[list[tuple[str, str]], dict[tuple[str, str], str]]:
    keys: list[tuple[str, str]] = []
    sources: dict[tuple[str, str], str] = {}
    for event in _iter_events(path):
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
    seen: Dict[Tuple[str, datetime], bool] = {}
    keys: list[tuple[str, str]] = []
    sources: dict[tuple[str, str], str] = {}
    per_file: list[dict[str, object]] = []

    for path, entries in webhistory_raw.iter_file_entries(root=raw_root):
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


def _build_index(events: Iterable[Tuple[int, str, str]]) -> Dict[str, List[int]]:
    index: Dict[str, List[int]] = {}
    for ts, url, _source in events:
        index.setdefault(url, []).append(ts)
    for ts_list in index.values():
        ts_list.sort()
    return index


def _has_match(index: Dict[str, List[int]], url: str, ts: int, tolerance: int) -> bool:
    candidates = index.get(url)
    if not candidates:
        return False
    lower = ts - tolerance
    pos = bisect_left(candidates, lower)
    if pos < len(candidates) and abs(candidates[pos] - ts) <= tolerance:
        return True
    return False


def _range(events: List[Tuple[int, str, str]]) -> Tuple[str | None, str | None]:
    if not events:
        return None, None
    seconds = [row[0] for row in events]
    start = datetime.fromtimestamp(min(seconds), tz=timezone.utc).isoformat()
    end = datetime.fromtimestamp(max(seconds), tz=timezone.utc).isoformat()
    return start, end


def _key_range(keys: list[tuple[str, str]]) -> dict[str, str | None]:
    if not keys:
        return {"start": None, "end": None}
    times = [iso_time for _norm, iso_time in keys]
    return {"start": min(times), "end": max(times)}


def _compare_gestalt(
    canonical: Path,
    candidate: Path,
    tolerance: int,
    sample: int,
) -> Dict[str, object]:
    canon_events = _load_events(canonical)
    candidate_events = _load_events(candidate)
    canon_index = _build_index(canon_events)
    candidate_index = _build_index(candidate_events)

    canon_missing = 0
    canon_missing_sample: List[Dict[str, str]] = []
    canon_matched = 0
    for ts, url, source in canon_events:
        if _has_match(candidate_index, url, ts, tolerance):
            canon_matched += 1
        else:
            canon_missing += 1
            if len(canon_missing_sample) < sample:
                canon_missing_sample.append(
                    {
                        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        "url": url,
                        "source": source,
                    }
                )

    candidate_only = 0
    candidate_only_sample: List[Dict[str, str]] = []
    candidate_matched = 0
    for ts, url, source in candidate_events:
        if _has_match(canon_index, url, ts, tolerance):
            candidate_matched += 1
        else:
            candidate_only += 1
            if len(candidate_only_sample) < sample:
                candidate_only_sample.append(
                    {
                        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        "url": url,
                        "source": source,
                    }
                )

    canon_start, canon_end = _range(canon_events)
    candidate_start, candidate_end = _range(candidate_events)

    return {
        "canonical": str(canonical),
        "candidate": str(candidate),
        "tolerance_seconds": tolerance,
        "canonical_count": len(canon_events),
        "candidate_count": len(candidate_events),
        "canonical_range": {"start": canon_start, "end": canon_end},
        "candidate_range": {"start": candidate_start, "end": candidate_end},
        "canonical_matched": canon_matched,
        "canonical_missing": canon_missing,
        "canonical_missing_sample": canon_missing_sample,
        "candidate_matched": candidate_matched,
        "candidate_only": candidate_only,
        "candidate_only_sample": candidate_only_sample,
    }


def _audit_webhistory(
    *,
    raw_root: Path,
    canonical: Path,
    merged: Path,
    tolerance: int,
    sample: int,
) -> Dict[str, object]:
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
    expected = [list(sig) for sig in signatures]
    return payload.get("files") == expected


if __name__ == "__main__":
    app()
