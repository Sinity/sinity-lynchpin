"""Materialize graph-facing ActivityWatch derived products."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from functools import lru_cache
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.cache import input_versions
from ..core.input_snapshot import input_snapshot, read_input_path, snapshot_today
from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core import parse as time_parse, primitives
from ..core.parse import as_local
from ..core.primitives import date_to_dt_range, logical_date
from ..sources.activitywatch import (
    attention,
    circadian,
    daily_activity,
    deep_work,
    focus_spans,
    fragmentation,
    loops,
    project_focus_days,
)
from ..sources.activitywatch_derived import (
    PRODUCT_KINDS,
    activitywatch_derived_dir,
    activitywatch_derived_generation_dir,
    activitywatch_derived_manifest_path,
    activitywatch_derived_path,
    resolve_derived_partition_path,
)
from ..sources.activitywatch_event_index import activitywatch_event_index_manifest_path
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import atomic_write_ndjson, guard_incremental_shrinkage, write_manifest


ACTIVITYWATCH_DERIVED_SCHEMA_VERSION = 6

# Bound on how many days of source events a single pass may hold in memory.
# The heavy allocations are the AWEvent objects and span intermediates inside
# the source generators, which scale with the requested window; chunking keeps
# the footprint window-length-independent (lynchpin-soa: a 760-day window
# reached ~5.6G resident and livelocked under a 2G cgroup cap).
DEFAULT_CHUNK_DAYS = 1


def materialize_activitywatch_derived(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
) -> dict[str, Any]:
    from ..sources import keylog
    from ..sources.terminal import canonical_atuin_history_path

    with input_snapshot() as snapshot:
        # The upstream producer has finished before this handler runs. Keep
        # its selected manifest and independent evidence fixed through every
        # chunk and, for candidate operations, subsequent graph promotion.
        snapshot.capture((activitywatch_event_index_manifest_path(),))
        if read_input_path(activitywatch_event_index_manifest_path()).exists():
            products = _load_json(activitywatch_event_index_manifest_path()).get("product_paths", {})
            if not isinstance(products, dict):
                raise MaterializationError("activitywatch_derived", reason="ActivityWatch index product_paths must be an object")
            snapshot.capture(tuple(Path(path) for path in products.values() if isinstance(path, str)))
        else:
            snapshot.capture((canonical_activitywatch_events_path(),
                              canonical_activitywatch_events_path().with_suffix(".manifest.json")))
        snapshot.capture((get_config().sleep_jsonl, canonical_atuin_history_path()))
        keyboard = tuple(keylog.log_files(ensure=False))
        open_dates = {snapshot.observed_at.date().isoformat(), datetime.now(UTC).date().isoformat()}
        open_keyboard = tuple(path for path in keyboard if path.stem in open_dates)
        snapshot.capture_collection(keylog._logs_root(), keyboard, append_only=open_keyboard)
        return _materialize_activitywatch_derived(start=start, end=end, root=root, chunk_days=chunk_days)


def _materialize_activitywatch_derived(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
) -> dict[str, Any]:
    existing_path = activitywatch_derived_manifest_path(root)
    previous = _load_json(existing_path)
    if (previous and previous.get("schema_version") != ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
            and (start is not None or end is not None)):
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason="retired logical-day calculations require an unbounded rebuild; partial reuse is unavailable",
        )
    start, end = _default_window(start, end)
    if end <= start:
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason="ActivityWatch derived materialization end must be after start",
        )
    if chunk_days < 1:
        raise MaterializationError(
            "activitywatch_derived_materialize", reason="chunk_days must be at least 1"
        )
    clipped_start, clipped_end = _clip_to_real_coverage(start, end)
    if clipped_end <= clipped_start:
        # The requested window has no overlap with the canonical AW events
        # product's real coverage (lynchpin-3yp: an ensure-cascade from a
        # dependent materializer forwarded a window reaching back to 2013,
        # and the generators dutifully computed "outages" for days
        # ActivityWatch never ran on). There is nothing to materialize —
        # return the existing product unchanged rather than fabricate rows.
        existing_path = activitywatch_derived_manifest_path(root)
        if existing_path.exists():
            return json.loads(existing_path.read_text(encoding="utf-8"))
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason=f"requested window {start}..{end} has no overlap with canonical ActivityWatch coverage",
        )
    published_manifest = activitywatch_derived_manifest_path(root)
    published_manifest.parent.mkdir(parents=True, exist_ok=True)
    staged_manifest = published_manifest.with_name(
        f".manifest-{uuid4().hex}.staging.json"
    )
    if published_manifest.exists():
        shutil.copyfile(published_manifest, staged_manifest)
    manifest: dict[str, Any] | None = None
    source_versions = input_versions(activitywatch_derived_input_files())
    repair_versions = activitywatch_repair_input_versions()
    closed_repair_versions = activitywatch_repair_input_versions(closed_only=True)
    _existing_partitions(previous, root=root, repair_window=(clipped_start, clipped_end))
    if not _repair_reuse_is_proven(previous, closed_repair_versions, clipped_start, clipped_end):
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason="independent repair evidence changed; rebuild all retained dates before recording its new identity",
        )
    if not _canonical_reuse_is_proven(previous, source_versions, clipped_start, clipped_end):
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason="canonical partitions changed outside the rebuilt frontier; rebuild affected retained dates",
        )
    chunk_start = clipped_start
    while chunk_start < clipped_end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), clipped_end)
        manifest = _materialize_window(
            start=chunk_start, end=chunk_end, root=root, manifest_path=staged_manifest
        )
        chunk_start = chunk_end
    assert manifest is not None  # loop runs at least once (clipped_end > clipped_start)
    if (input_versions(activitywatch_derived_input_files()) != source_versions
            or activitywatch_repair_input_versions(closed_only=True) != closed_repair_versions):
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason="ActivityWatch derivation inputs changed during construction; retaining the published generation",
        )
    manifest["input_versions"] = source_versions
    manifest["repair_input_versions"] = repair_versions
    manifest["closed_repair_input_versions"] = closed_repair_versions
    manifest["window_start"] = clipped_start.isoformat()
    manifest["window_end"] = clipped_end.isoformat()
    write_manifest(staged_manifest, manifest)
    # Each chunk stays memory-bounded; the one pointer is published only when
    # every chunk succeeded. Failed candidates remain recoverable and unselected.
    staged_manifest.replace(published_manifest)
    return manifest


def _clip_to_real_coverage(start: date, end: date) -> tuple[date, date]:
    """Clip the half-open ``[start, end)`` request to indexed AW coverage.

    The logical-day event index is the active bounded product. The legacy
    canonical NDJSON remains an offline recovery carrier, so it is only a
    fallback when no index manifest has been published yet. Outside the real
    source span there is no event data, and asking generators to infer a day
    with no ActivityWatch coverage fabricates outages and other derived rows.
    """
    manifest = activitywatch_event_index_manifest_path()
    if not read_input_path(manifest).exists():
        manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not read_input_path(manifest).exists():
        # No materialized carrier to clip against. This is the normal test
        # state when source generators are monkeypatched directly.
        return start, end
    payload = json.loads(read_input_path(manifest).read_text(encoding="utf-8"))
    first_raw = payload.get("first_date")
    last_raw = payload.get("last_date")
    if not first_raw or not last_raw:
        return start, end
    floor = date.fromisoformat(str(first_raw))
    ceiling = date.fromisoformat(str(last_raw)) + timedelta(days=1)  # end is exclusive
    return max(start, floor), min(end, ceiling)


def _load_json(path: Path) -> dict[str, Any]:
    path = read_input_path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _materialize_window(
    *,
    start: date,
    end: date,
    root: Path | None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path or activitywatch_derived_manifest_path(root)
    output_dir = activitywatch_derived_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    end_inclusive = end - timedelta(days=1)
    lower, upper = date_to_dt_range(start, end_inclusive)
    start_dt, end_dt = as_local(lower), as_local(upper)

    window_rows = {
        "focus_spans": [
            _focus_span_row(span)
            for span in focus_spans(
                start=start_dt,
                end=end_dt,
                min_duration_s=60.0,
                enrich_polylogue=True,
                ensure=False,
            )
        ],
        "project_focus_days": [
            _project_focus_day_row(row)
            for row in project_focus_days(start=start_dt, end=end_dt, ensure=False)
        ],
        "daily_activity": [
            _daily_activity_row(row)
            for row in daily_activity(start=start, end=end_inclusive, ensure=False)
        ],
        "deep_work": [
            _deep_work_row(row)
            for row in deep_work(start=start_dt, end=end_dt, ensure=False)
        ],
        "circadian": [
            _circadian_row(row)
            for row in circadian(start=start, end=end_inclusive, ensure=False)
        ],
        "loops": [
            _loop_row(row) for row in loops(start=start_dt, end=end_dt, ensure=False)
        ],
        "fragmentation": [
            _fragmentation_row(row)
            for row in fragmentation(start=start, end=end_inclusive, ensure=False)
        ],
        "attention": [
            _attention_row(row)
            for row in attention(start=start, end=end_inclusive, ensure=False)
        ],
    }

    previous = _load_json(manifest_path)
    paths, partition_counts = _existing_partitions(
        previous, root=root, repair_window=(start, end)
    )
    migration = not (
        isinstance(previous.get("product_paths"), dict)
        and isinstance(previous.get("partition_row_counts"), dict)
    )
    if migration:
        # Only flat carriers need storage conversion. A temporal recipe change
        # over already partitioned products must not reintroduce their obsolete
        # flat predecessors or append rows into retained Path references.
        for kind in PRODUCT_KINDS:
            for row in _read_existing_rows(activitywatch_derived_path(kind, root)):
                day = _row_logical_date(kind, row).isoformat()
                paths[kind].setdefault(day, [])
                paths[kind][day].append(row)

    generation = f"generation-{uuid4().hex}"
    generation_dir = activitywatch_derived_generation_dir(generation, root)
    staging_dir = generation_dir.with_name(f".{generation}.staging")
    staging_dir.mkdir(parents=True)
    next_paths: dict[str, dict[str, str]] = {
        kind: {
            day: str(path) for day, path in path_map.items() if isinstance(path, Path)
        }
        for kind, path_map in paths.items()
    }
    next_counts: dict[str, dict[str, int]] = {
        kind: dict(counts) for kind, counts in partition_counts.items()
    }

    for kind in PRODUCT_KINDS:
        rows_by_day: dict[str, list[dict[str, object]]] = {}
        if migration:
            for day, legacy_rows in paths[kind].items():
                assert isinstance(legacy_rows, list)
                rows_by_day[day] = [
                    row
                    for row in legacy_rows
                    if not (start <= _row_logical_date(kind, row) < end)
                ]
        for row in window_rows[kind]:
            day = _row_logical_date(kind, row).isoformat()
            if start <= date.fromisoformat(day) < end:
                rows_by_day.setdefault(day, []).append(row)
        for day in tuple(next_paths[kind]):
            if start <= date.fromisoformat(day) < end:
                next_paths[kind].pop(day, None)
                next_counts[kind].pop(day, None)
        for day, rows in rows_by_day.items():
            if not rows:
                continue
            target = staging_dir / kind / f"{day}.ndjson"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_ndjson(
                target, sorted(rows, key=lambda row: _row_sort_key(kind, row))
            )
            next_paths[kind][day] = str(generation_dir / kind / target.name)
            next_counts[kind][day] = len(rows)

    generation_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.replace(generation_dir)
    row_counts = {kind: sum(next_counts[kind].values()) for kind in PRODUCT_KINDS}
    guard_incremental_shrinkage(
        activitywatch_derived_manifest_path(root),
        sum(row_counts.values()),
        dataset="lynchpin.activitywatch_derived",
    )

    input_files = activitywatch_derived_input_files()
    covered_dates = _merge_covered_dates(
        root=root, start=start, end=end, manifest_path=manifest_path
    )
    manifest = {
        "dataset": "lynchpin.activitywatch_derived",
        "schema_version": ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
        "generation": generation,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "date_boundary": "logical_06:00_local",
        "product_paths": next_paths,
        "partition_row_counts": next_counts,
        "row_counts": row_counts,
        "row_count": sum(row_counts.values()),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(manifest_path, manifest)
    return manifest


def activitywatch_derived_input_files() -> tuple[Path, ...]:
    # The index producer owns native DB/WAL freshness. These readers consume
    # the published index and its immutable partitions, including declared
    # paths that have gone missing (absence must invalidate the product).
    manifest = activitywatch_event_index_manifest_path()
    if read_input_path(manifest).exists():
        payload = _load_json(manifest)
        products = payload.get("product_paths", {})
        paths = (
            tuple(Path(path) for _, path in sorted(products.items()) if isinstance(path, str))
            if isinstance(products, dict) else ()
        )
        return (manifest, *paths)
    return tuple(path for path in (canonical_activitywatch_events_path(),) if read_input_path(path).exists())


def activitywatch_repair_input_versions(*, closed_only: bool = False) -> list[dict[str, Any]]:
    """Record independent repair evidence, including optional absence.

    Missing evidence stays missing; its later arrival changes this identity.
    Atuin's native database is owned by its producer, while repair reads the
    canonical history. Keyboard repair reads the capture files directly.
    """
    from ..sources.keylog import log_files
    from ..sources.terminal import canonical_atuin_history_path

    keyboard = log_files(
            ensure=False,
            end=(snapshot_today() or datetime.now(UTC).date()) - timedelta(days=1) if closed_only else None,
        )
    result = []
    for source, paths in (("sleep", (get_config().sleep_jsonl,)),
                          ("atuin", (canonical_atuin_history_path(),)),
                          ("keylog", keyboard)):
        if source == "atuin":
            path = paths[0]
            signature = json.dumps(input_versions(paths), sort_keys=True) + f":{time_parse.local_tz()}:{primitives.DAY_BOUNDARY_HOUR}"
            result.extend(_atuin_day_versions(path, signature))
            continue
        for item in input_versions(paths):
            item["source"] = source
            if source == "keylog":
                try:
                    item["date"] = date.fromisoformat(Path(item["path"]).stem).isoformat()
                except ValueError:
                    item["date"] = None
            result.append(item)
    return sorted(result, key=lambda item: (item["path"], item.get("date") or ""))


@lru_cache(maxsize=4)
def _atuin_day_versions(path: Path, signature: str) -> tuple[dict[str, Any], ...]:
    del signature
    selected = read_input_path(path)
    if not selected.exists():
        return ({"path": str(path), "source": "atuin", "date": None, "available": False, "stat": None},)
    if not selected.is_file():
        raise MaterializationError("activitywatch_derived", reason="Atuin repair input is not a regular file")
    days: dict[str, list[bytes]] = {}
    with selected.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            stamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            day = logical_date(stamp).isoformat()
            normalized = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            days.setdefault(day, []).append(hashlib.sha256(normalized).digest())
    marker = {"path": str(path), "source": "atuin", "date": None, "available": True}
    return (marker, *tuple({"path": str(path), "source": "atuin", "date": day, "available": True,
                  "sha256": hashlib.sha256(b"".join(sorted(rows))).hexdigest(), "row_count": len(rows),
                  "digest_scope": "normalized_logical_day_records"}
                 for day, rows in sorted(days.items())))


def _repair_reuse_is_proven(previous: dict[str, Any], current: list[dict[str, Any]], start: date, end: date) -> bool:
    outside = {date.fromisoformat(day) for day in previous.get("covered_dates", [])
               if isinstance(day, str) and not start.isoformat() <= day < end.isoformat()}
    if not outside:
        return True
    # The full recorded identity includes yesterday's then-open file. Its
    # transition to a closed input need only rebuild the dates it can affect.
    expected = previous.get("repair_input_versions")
    if expected is None:
        return previous.get("closed_repair_input_versions") == current
    old = {(item["path"], item.get("date")): item for item in expected}
    new = {(item["path"], item.get("date")): item for item in current}
    for path in old.keys() | new.keys():
        before, after = old.get(path), new.get(path)
        item = after or before
        assert item is not None
        if item.get("source") == "atuin" and item.get("date"):
            if before != after:
                affected = _atuin_affected_dates(date.fromisoformat(item["date"]), outside)
                if affected is None or outside.intersection(affected):
                    return False
        elif item.get("source") == "keylog" and item.get("date"):
            day = date.fromisoformat(item["date"])
            if day >= (snapshot_today() or datetime.now(UTC).date()):
                continue  # still-open inputs are observed, not stability gates
            if before != after and any(abs((retained - day).days) <= 1 for retained in outside):
                return False
        elif before != after:
            return False
    return True


def _atuin_affected_dates(day: date, retained: set[date]) -> set[date] | None:
    # Repair reads complete AFK/window intervals before clipping to a requested
    # day. A terminal event can rescue a long interval far beyond its own day.
    from ..sources.activitywatch_raw import afk_events, window_events
    start, end = date_to_dt_range(day, day)
    result = {day}
    try:
        for events in (afk_events(start=start, end=end, ensure=False),
                       window_events(start=start, end=end, ensure=False)):
            for event in events:
                first = logical_date(event.start)
                last = logical_date(event.end - timedelta(microseconds=1))
                result.update(value for value in retained if first <= value <= last)
    except Exception:
        return None  # Unknown influence cannot justify retaining older dates.
    return result


def activitywatch_derived_refresh_window(window: tuple[date, date]) -> tuple[date, date]:
    """Include retained suffixes affected by changed canonical partitions."""
    previous = _load_json(activitywatch_derived_manifest_path())
    current = input_versions(activitywatch_derived_input_files())
    start, end = window
    old_repair = previous.get("repair_input_versions", [])
    if (isinstance(old_repair, list)
            and any(item.get("source") == "atuin" and "available" in item for item in old_repair)
            and not any(item.get("source") == "atuin" and "available" not in item for item in old_repair)):
        old_days = {(item["path"], item.get("date")): item for item in old_repair if item.get("source") == "atuin"}
        new_days = {(item["path"], item.get("date")): item for item in activitywatch_repair_input_versions(closed_only=True)
                    if item.get("source") == "atuin"}
        retained_days = {date.fromisoformat(day) for day in previous.get("covered_dates", [])}
        for key in old_days.keys() | new_days.keys():
            item = new_days.get(key) or old_days[key]
            if item.get("date") and old_days.get(key) != new_days.get(key):
                affected = _atuin_affected_dates(date.fromisoformat(item["date"]), retained_days)
                if affected:
                    start = min(start, min(affected))
                    end = max(end, max(affected) + timedelta(days=1))
        window = start, end
    if _canonical_reuse_is_proven(previous, current, start, end):
        return window
    expected = previous.get("input_versions")
    if not isinstance(expected, list):
        return window  # The materializer must refuse unproven history.
    pointer = str(activitywatch_event_index_manifest_path())
    old = {item["path"]: item for item in expected if item["path"] != pointer}
    new = {item["path"]: item for item in current if item["path"] != pointer}
    try:
        changed = [date.fromisoformat(Path(path).stem)
                   for path in old.keys() | new.keys() if old.get(path) != new.get(path)]
        retained = [date.fromisoformat(day) for day in previous.get("covered_dates", [])]
    except ValueError:
        return window  # Undated carriers require an explicit full rebuild.
    if not changed or not retained:
        return window
    return min(start, min(changed) - timedelta(days=1)), max(end, max(retained) + timedelta(days=1))


def _canonical_reuse_is_proven(previous: dict[str, Any], current: list[dict[str, Any]], start: date, end: date) -> bool:
    outside = {date.fromisoformat(day) for day in previous.get("covered_dates", [])
               if isinstance(day, str) and not start.isoformat() <= day < end.isoformat()}
    if not outside:
        return True
    expected = previous.get("input_versions")
    if not isinstance(expected, list):
        return False
    pointer = str(activitywatch_event_index_manifest_path())
    old = {item["path"]: item for item in expected if item["path"] != pointer}
    new = {item["path"]: item for item in current if item["path"] != pointer}
    changed = [path for path in old.keys() | new.keys() if old.get(path) != new.get(path)]
    for path in changed:
        try:
            day = date.fromisoformat(Path(path).stem)
        except ValueError:
            return False
        # Indexed events can extend beyond their starting day. Rebuild the
        # retained suffix, rather than assuming every event lasts one day.
        if any(retained >= day - timedelta(days=1) for retained in outside):
            return False
    return True


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    manifest = activitywatch_event_index_manifest_path()
    if not read_input_path(manifest).exists():
        manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not read_input_path(manifest).exists():
        raise FileNotFoundError(
            "ActivityWatch index and recovery carrier manifests are missing; run "
            "python -m lynchpin.ingest.activitywatch_event_index_materialize --full first"
        )
    payload = json.loads(read_input_path(manifest).read_text(encoding="utf-8"))
    first = start or date.fromisoformat(str(payload["first_date"]))
    last_inclusive = date.fromisoformat(str(payload["last_date"]))
    return first, end or (last_inclusive + timedelta(days=1))


def _existing_partitions(
    manifest: dict[str, Any],
    *,
    root: Path | None = None,
    repair_window: tuple[date, date] | None = None,
) -> tuple[dict[str, dict[str, Path]], dict[str, dict[str, int]]]:
    raw_paths = manifest.get("product_paths")
    raw_counts = manifest.get("partition_row_counts")
    paths: dict[str, dict[str, Path]] = {kind: {} for kind in PRODUCT_KINDS}
    counts: dict[str, dict[str, int]] = {kind: {} for kind in PRODUCT_KINDS}
    if not isinstance(raw_paths, dict) or not isinstance(raw_counts, dict):
        return paths, counts
    for kind in PRODUCT_KINDS:
        product_paths = raw_paths.get(kind)
        product_counts = raw_counts.get(kind)
        if not isinstance(product_paths, dict) or not isinstance(product_counts, dict):
            return {name: {} for name in PRODUCT_KINDS}, {name: {} for name in PRODUCT_KINDS}
        for day, value in product_paths.items():
            path = resolve_derived_partition_path(str(value), root)
            count = product_counts.get(day)
            if not path.is_file() and repair_window is not None:
                start, end = repair_window
                partition_day = date.fromisoformat(str(day))
                if start <= partition_day < end and isinstance(count, int):
                    continue
            if not path.is_file() or not isinstance(count, int):
                raise MaterializationError(
                    "activitywatch_derived",
                    reason=f"manifest partition is unavailable or lacks its count: {kind}/{day} ({path})",
                )
            paths[kind][str(day)] = path
            counts[kind][str(day)] = count
    return paths, counts


def _merge_covered_dates(
    *,
    root: Path | None,
    start: date,
    end: date,
    verified_bounds: tuple[date, date] | None = None,
    manifest_path: Path | None = None,
) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=manifest_path or activitywatch_derived_manifest_path(root),
        start=start,
        end=end,
        verified_bounds=verified_bounds,
    )


def _read_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _row_logical_date(kind: str, row: dict[str, object]) -> date:
    if kind in {"focus_spans", "deep_work", "loops"}:
        return logical_date(datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00")))
    return date.fromisoformat(str(row["date"]))


def _row_sort_key(kind: str, row: dict[str, object]) -> tuple[str, str, str]:
    day = _row_logical_date(kind, row).isoformat()
    start = str(row.get("start") or "")
    return day, start, json.dumps(row, ensure_ascii=False, sort_keys=True)


def _write_ndjson(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_ndjson(path, rows)


def _focus_span_row(span: Any) -> dict[str, object]:
    return {
        "start": span.start.isoformat(),
        "end": span.end.isoformat(),
        "kind": span.kind,
        "app": span.app,
        "title": span.title,
        "mode": span.mode,
        "project": span.project,
        "duration_s": round(float(span.duration_s), 3),
        "keypress_count": int(getattr(span, "keypress_count", 0)),
        "keylog_state": getattr(span, "keylog_state", "not_requested"),
        "positive_observations": getattr(span, "positive_observations", None),
    }


def _project_focus_day_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "project": row.project,
        "duration_s": round(float(row.duration_s), 3),
    }


def _daily_activity_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "active_hours": round(float(row.active_hours), 3),
        "deep_work_min": round(float(row.deep_work_min), 3),
        "fragmentation_score": round(float(row.fragmentation_score), 6),
        "project_count": int(row.project_count),
        "dominant_mode": row.dominant_mode,
        "dominant_project": row.dominant_project,
        "hourly_active": [round(float(value), 3) for value in row.hourly_active],
        "outage_hours": round(float(row.outage_hours), 3),
        "presence_active_hours": round(float(row.presence_active_hours), 3),
        "presence_typing_hours": round(float(row.presence_typing_hours), 3),
        "presence_data_gap_hours": round(float(row.presence_data_gap_hours), 3),
    }


def _deep_work_row(row: Any) -> dict[str, object]:
    return {
        "start": row.start.isoformat(),
        "end": row.end.isoformat(),
        "duration_min": round(float(row.duration_min), 3),
        "project": row.project,
        "mode": row.mode,
        "focus_ratio": round(float(row.focus_ratio), 6),
        "app_switches": int(row.app_switches),
    }


def _circadian_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "hour": int(row.hour),
        "active_min": round(float(row.active_min), 3),
        "recovery_min": round(float(row.recovery_min), 3),
        "dominant_mode": row.dominant_mode,
        "dominant_project": row.dominant_project,
    }


def _loop_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "start": row.start.isoformat(),
        "end": row.end.isoformat(),
        "duration_min": round(float(row.duration_min), 3),
        "span_count": int(row.span_count),
        "switch_count": int(row.switch_count),
        "context_a": row.context_a,
        "context_b": row.context_b,
        "dominant_project": row.dominant_project,
    }


def _fragmentation_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "total_switches": int(row.total_switches),
        "avg_focus_min": round(float(row.avg_focus_min), 3),
        "longest_focus_min": round(float(row.longest_focus_min), 3),
        "fragmentation": round(float(row.fragmentation), 6),
    }


def _attention_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "entropy": round(float(row.entropy), 6),
        "gini": round(float(row.gini), 6),
        "top_project": row.top_project,
        "project_count": int(row.project_count),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize graph-facing ActivityWatch derived products")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    args = parser.parse_args(argv)
    report = materialize_activitywatch_derived(
        start=args.start, end=args.end, root=args.root, chunk_days=args.chunk_days
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
