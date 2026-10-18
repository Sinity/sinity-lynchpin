"""Materialize ActivityWatch content rollups from canonical title metadata."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..core.io import atomic_exchange_paths, latest_mtime_iso
from ..core.errors import MaterializationError
from ..core.cache import input_versions, manifest_versions_current
from ..core.parse import as_local
from ..core.primitives import date_to_dt_range, duration_s, split_by_day
from ..sources.activity_content import activity_content_daily_path, activity_title_usage_path
from ..sources.activitywatch import focus_spans
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from ..sources.title_metadata import hash_title, load_title_classification_map, normalize_title, title_metadata_path
from ..sources.title_metadata_rules import classify_title_via_rules
from ._manifest import atomic_write_ndjson, product_lock, write_manifest
from ..materializers.partition_store import ArtifactStore, ProductPartitionKey, deterministic_input_digest


ACTIVITY_CONTENT_SCHEMA_VERSION = 2
DEFAULT_CHUNK_DAYS = 30
RECENT_CHUNK_DAYS = 1
RECENT_WINDOW_DAYS = 30


def activity_content_computed_windows(manifest: dict[str, Any]) -> tuple[tuple[date, date], ...]:
    """Validated computation domains, independent of observed row bounds."""
    proof = manifest.get("computed_coverage")
    versions = manifest.get("input_versions")
    if (manifest.get("schema_version") != ACTIVITY_CONTENT_SCHEMA_VERSION
            or not isinstance(proof, dict) or proof.get("version") != 1
            or not isinstance(versions, list) or not versions
            or proof.get("input_digest") != deterministic_input_digest(versions)):
        return ()
    windows = proof.get("windows")
    if not isinstance(windows, list) or not windows:
        return ()
    parsed: list[tuple[date, date]] = []
    try:
        for window in windows:
            if not isinstance(window, dict):
                return ()
            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end"])
            if end <= start or parsed and start <= parsed[-1][1]:
                return ()
            parsed.append((start, end))
    except (KeyError, TypeError, ValueError):
        return ()
    return tuple(parsed)


def _computed_coverage(previous: dict[str, Any], start: date, end: date,
                       versions: list[dict[str, Any]]) -> dict[str, Any]:
    # Global upstream revisions have no proven append-only/date-local contract.
    # Preserve payload history but never relabel it as computed for a new input.
    previous_windows = activity_content_computed_windows(previous) if previous.get("input_versions") == versions else ()
    merged: list[tuple[date, date]] = []
    for lower, upper in sorted((*previous_windows, (start, end))):
        if merged and lower <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    return {
        "version": 1,
        "input_digest": deterministic_input_digest(versions),
        "windows": [{"start": lower.isoformat(), "end": upper.isoformat()} for lower, upper in merged],
        "predecessor_generation_id": previous.get("generation_id") if previous_windows else None,
    }


class _TitleUsageStore:
    """Persistent, day-partitioned accumulator for high-cardinality title usage rows.

    Two tables back the exported title_usage.ndjson:

    - ``title_dim``: one row per (title_hash, app) — the title's latest-seen
      classification. Upserted whenever a title is touched by whatever window
      is currently being (re)processed.
    - ``title_usage_daily``: one row per (title_hash, app, day) — that title's
      contribution on that specific day. This is what makes reprocessing a
      narrow window safe and idempotent: :meth:`reset_window` deletes exactly
      the day-rows about to be recomputed, then :meth:`add` repopulates them,
      so re-running the same window twice cannot double-count, and a day
      outside the window is never touched at all.

    The exported per-title lifetime totals (focused_seconds, span_count,
    first_date, last_date) are a SUM/MIN/MAX over ``title_usage_daily``,
    computed at export time — the file always reflects the union of every
    day this store has ever seen, not just the most recently processed
    window. This is what lets a materialize call recompute only the days it
    was asked about instead of the dataset's entire history every time
    (lynchpin-d36): the store itself is the durable "already materialized"
    record, not an ephemeral per-run scratch file.
    """

    _columns = (
        "title_hash",
        "app",
        "normalized_title",
        "example_title",
        "focused_seconds",
        "span_count",
        "first_date",
        "last_date",
        "matched",
        "classification_source",
        "confidence",
        "activity",
        "content_type",
        "attention_level",
        "topic_category",
        "platform",
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self._transactional = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS title_dim (
                title_hash TEXT NOT NULL,
                app TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                example_title TEXT NOT NULL,
                matched INTEGER NOT NULL,
                classification_source TEXT,
                confidence REAL,
                activity TEXT,
                content_type TEXT,
                attention_level TEXT,
                topic_category TEXT,
                platform TEXT,
                PRIMARY KEY (title_hash, app)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS title_usage_daily (
                title_hash TEXT NOT NULL,
                app TEXT NOT NULL,
                day TEXT NOT NULL,
                focused_seconds REAL NOT NULL,
                span_count INTEGER NOT NULL,
                PRIMARY KEY (title_hash, app, day)
            )
            """
        )
        self.connection.commit()

    def reset_window(self, start: date, end: date) -> None:
        """Clear persisted per-day facts for ``[start, end)``.

        Must be called once before reprocessing that range so the day-rows
        this run recomputes replace the old ones exactly, instead of
        accumulating on top of them.
        """
        self.connection.execute(
            "DELETE FROM title_usage_daily WHERE day >= ? AND day < ?",
            (start.isoformat(), end.isoformat()),
        )
        self.commit()

    def ensure_row(
        self,
        *,
        title_hash: str,
        app: str,
        normalized_title: str,
        example_title: str,
        classification: Any,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO title_dim (
                title_hash, app, normalized_title, example_title, matched,
                classification_source, confidence, activity, content_type,
                attention_level, topic_category, platform
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (title_hash, app) DO UPDATE SET
                normalized_title = excluded.normalized_title,
                example_title = excluded.example_title,
                matched = excluded.matched,
                classification_source = excluded.classification_source,
                confidence = excluded.confidence,
                activity = excluded.activity,
                content_type = excluded.content_type,
                attention_level = excluded.attention_level,
                topic_category = excluded.topic_category,
                platform = excluded.platform
            """,
            (
                title_hash,
                app,
                normalized_title,
                example_title,
                int(classification is not None),
                getattr(classification, "classification_source", None),
                getattr(classification, "confidence", None),
                getattr(classification, "activity", None),
                getattr(classification, "content_type", None),
                getattr(classification, "attention_level", None),
                getattr(classification, "topic_category", None),
                getattr(classification, "platform", None),
            ),
        )

    def add(self, *, title_hash: str, app: str, day: date, seconds: float) -> None:
        day_s = day.isoformat()
        self.connection.execute(
            """
            INSERT INTO title_usage_daily (title_hash, app, day, focused_seconds, span_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT (title_hash, app, day) DO UPDATE SET
                focused_seconds = focused_seconds + excluded.focused_seconds,
                span_count = span_count + 1
            """,
            (title_hash, app, day_s, seconds),
        )

    def __enter__(self) -> _TitleUsageStore:
        self._transactional = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()

    def commit(self) -> None:
        if not self._transactional:
            self.connection.commit()

    def iter_rows(self) -> Any:
        query = """
            SELECT
                d.title_hash, d.app, d.normalized_title, d.example_title,
                SUM(f.focused_seconds), SUM(f.span_count),
                MIN(f.day), MAX(f.day),
                d.matched, d.classification_source, d.confidence,
                d.activity, d.content_type, d.attention_level, d.topic_category, d.platform
            FROM title_dim d
            JOIN title_usage_daily f ON f.title_hash = d.title_hash AND f.app = d.app
            GROUP BY d.title_hash, d.app
            ORDER BY SUM(f.focused_seconds) DESC, d.app, d.normalized_title
        """
        for values in self.connection.execute(query):
            row = dict(zip(self._columns, values, strict=True))
            row["matched"] = bool(row["matched"])
            yield {key: value for key, value in row.items() if value is not None}

    def count(self) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM title_dim d
                    JOIN title_usage_daily f ON f.title_hash = d.title_hash AND f.app = d.app
                    GROUP BY d.title_hash, d.app
                )
                """
            ).fetchone()[0]
        )

    def unmatched_count(self) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM title_dim d
                    JOIN title_usage_daily f ON f.title_hash = d.title_hash AND f.app = d.app
                    WHERE d.matched = 0
                    GROUP BY d.title_hash, d.app
                )
                """
            ).fetchone()[0]
        )

    def top_unmatched(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT d.app, d.normalized_title, SUM(f.focused_seconds), SUM(f.span_count)
            FROM title_dim d
            JOIN title_usage_daily f ON f.title_hash = d.title_hash AND f.app = d.app
            WHERE d.matched = 0
            GROUP BY d.title_hash, d.app
            ORDER BY SUM(f.focused_seconds) DESC
            LIMIT 20
            """
        )
        return [
            {
                "app": app,
                "normalized_title": normalized_title,
                "focused_seconds": round(float(focused_seconds), 3),
                "span_count": span_count,
            }
            for app, normalized_title, focused_seconds, span_count in rows
        ]

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


def _bundle_names(output: Path) -> tuple[str, ...]:
    return (output.name, output.with_suffix(".manifest.json").name,
            "title_usage.ndjson", "title_usage.facts.sqlite3",
            f".{output.stem}.partitions", ".title_usage.partitions")


def _copy_bundle(source: Path, destination: Path, names: tuple[str, ...]) -> None:
    destination.mkdir(mode=0o700, parents=True)
    for name in names:
        original = source / name
        target = destination / name
        if not original.exists():
            continue
        if original.is_dir():
            # Artifact bytes are immutable; manifest updates replace their inode.
            shutil.copytree(original, target, copy_function=os.link)
        elif name.endswith(".sqlite3"):
            # Even a legacy accumulator with a live WAL needs a native snapshot.
            with closing(sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(target)) as writer:
                    reader.backup(writer)
        else:
            os.link(original.resolve(), target)


def _select_bundle(root: Path, generation: Path) -> None:
    temporary = root / f".current-{uuid.uuid4().hex}"
    temporary.symlink_to(generation.name, target_is_directory=True)
    os.replace(temporary, root / "current")
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _adopt_public_path(public: Path, link_target: Path, root: Path) -> None:
    temporary = public.with_name(f".link-{uuid.uuid4().hex}")
    temporary.symlink_to(link_target, target_is_directory=public.name.endswith(".partitions"))
    if public.exists() or public.is_symlink():
        # Exchange also supports nonempty native store directories. At no point
        # does a reader lose the old path; its original object remains here.
        atomic_exchange_paths(temporary, public)
        _fsync_directory(public.parent)
        preserved = root / f"original-{uuid.uuid4().hex}-{public.name}"
        os.replace(temporary, preserved)
        _fsync_directory(root)
    else:
        os.replace(temporary, public)


def materialize_activity_content(
    *, start: date | None = None, end: date | None = None, output: Path | None = None,
    allow_history_bootstrap: bool = False,
) -> dict[str, Any]:
    """Stage the accumulator and both products, then select them together.

    Old and interrupted generations remain recoverable. Stable file public paths
    traverse one directory pointer; readers resolve it before opening a product.
    """
    allow_history_bootstrap = allow_history_bootstrap or (start is None and end is None)
    output = output or activity_content_daily_path()
    names = _bundle_names(output)
    root = output.parent / f".{output.stem}.generations"
    with product_lock(output):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = root / "current"
        if not current.is_symlink():
            legacy = root / f"legacy-{uuid.uuid4().hex}"
            _copy_bundle(output.parent, legacy, names)
            _flush_bundle(legacy)
            _select_bundle(root, legacy)
        # Preserve original public paths before replacing them with selected links.
        # An interrupted adoption serves the same legacy bytes through either path.
        for name in names:
            public = output.parent / name
            link_target = Path(root.name) / "current" / name
            if public.is_symlink() and os.readlink(public) == str(link_target):
                continue
            _adopt_public_path(public, link_target, root)
        _fsync_directory(output.parent)
        previous = current.resolve(strict=True)
        selected_output = previous / output.name
        selected_manifest = _read_manifest(selected_output.with_suffix(".manifest.json"))
        history = _read_existing_daily(selected_output) if selected_output.exists() else {}
        incompatible = bool(history) and selected_manifest.get("schema_version") != ACTIVITY_CONTENT_SCHEMA_VERSION
        if incompatible:
            if start is not None or end is not None:
                raise MaterializationError(
                    "activity_content",
                    reason="retired temporal calculations require an unbounded rebuild; partial reuse is unavailable",
                )
            start, end = _default_window(None, None)
            start = min(start, min(history))
            end = max(end, max(history) + timedelta(days=1))
        if not incompatible and (start is None or end is None):
            files = activity_content_input_files()
            daily_store = ArtifactStore(previous / f".{output.stem}.partitions")
            usage_store = ArtifactStore(previous / ".title_usage.partitions")
            selected_manifest = _read_manifest(selected_output.with_suffix(".manifest.json"))
            if (selected_manifest.get("generation_id") == previous.name
                    and daily_store.selection_is_readable() and usage_store.selection_is_readable()
                    and daily_store.metadata.get("input_signature") == _input_signature(files)
                    and manifest_versions_current(selected_manifest, files)):
                return selected_manifest
        generation = root / f"gen-{uuid.uuid4().hex}"
        if incompatible:
            # Build every retained day afresh, retaining the previous bundle if
            # acquisition or validation fails. Never relabel old calculations.
            generation.mkdir(mode=0o700)
        else:
            _copy_bundle(previous, generation, names)
        staged_output = generation / output.name
        manifest = _materialize_activity_content(start=start, end=end, output=staged_output,
                                                 allow_history_bootstrap=allow_history_bootstrap)
        manifest["generation_id"] = generation.name
        write_manifest(staged_output.with_suffix(".manifest.json"), manifest)
        if not _usage_store_complete(generation / "title_usage.facts.sqlite3", _read_existing_daily(staged_output)):
            raise MaterializationError("activity_content", reason="staged accumulator and daily totals disagree")
        # Closed SQLite facts, outputs and store manifests must all survive before
        # the one publication rename. No rollback can undo a selected generation.
        _flush_bundle(generation)
        _select_bundle(root, generation)
        return manifest


def _flush_bundle(generation: Path) -> None:
    for path in generation.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for directory in sorted((path for path in generation.rglob("*") if path.is_dir()),
                            key=lambda path: len(path.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(generation)


def _usage_store_complete(path: Path, daily: dict[date, dict[str, Any]]) -> bool:
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            observed = dict(connection.execute("SELECT day, SUM(focused_seconds) FROM title_usage_daily GROUP BY day"))
        expected = {day.isoformat(): float(row["focused_seconds"]) for day, row in daily.items()}
        # Daily exports round to milliseconds independently; rounding error must
        # not accumulate across history or hide facts assigned to the wrong day.
        return all(abs(float(observed.get(day, 0)) - expected.get(day, 0)) <= 0.001
                   for day in observed.keys() | expected.keys())
    except (sqlite3.Error, OSError):
        return False


def _materialize_activity_content(
    *,
    start: date | None = None,
    end: date | None = None,
    output: Path | None = None,
    allow_history_bootstrap: bool = False,
) -> dict[str, Any]:
    bounded_request = start is not None and end is not None
    start, end = _default_window(start, end)
    default_output = activity_content_daily_path()
    output = output or default_output
    usage_output = activity_title_usage_path() if output == default_output else output.with_name("title_usage.ndjson")
    input_files = activity_content_input_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    usage_output.parent.mkdir(parents=True, exist_ok=True)
    partition_store = ArtifactStore(output.with_name(f".{output.stem}.partitions"))
    usage_partition_store = ArtifactStore(usage_output.with_name(f".{usage_output.stem}.partitions"))
    input_signature = _input_signature(input_files)
    acquired_versions = input_versions(input_files)
    classifications = load_title_classification_map()

    existing_by_day = _read_partitioned_daily(partition_store)
    if not existing_by_day:
        existing_by_day = _read_existing_daily(output)
        if existing_by_day:
            migrated_selected: dict[ProductPartitionKey, Any] = {}
            for day, row in existing_by_day.items():
                migrated_selected[ProductPartitionKey.day("activity_content.daily", day)] = partition_store.put(
                    ProductPartitionKey.day("activity_content.daily", day),
                    _encode_row(row), format="ndjson", row_count=1,
                    first_date=day, last_date=day,
                )
            partition_store.publish(migrated_selected, metadata={"migration": "legacy-monolith", "validated": True})

    facts_path = usage_output.with_name(f"{usage_output.stem}.facts.sqlite3")
    _migrate_usage_store(usage_partition_store, usage_output)
    if not _usage_store_complete(facts_path, existing_by_day) and existing_by_day:
        if bounded_request and not allow_history_bootstrap:
            raise MaterializationError(
                "activity_content",
                reason="incomplete historical title-usage accumulator requires explicit full repair; bounded materialization cannot widen into history",
            )
        # A created database is not proof that bootstrap committed. Reconstruct
        # preserved daily history when the accumulator is missing or disagrees;
        # otherwise a narrow retry can silently discard earlier title facts.
        start = min(start, min(existing_by_day))
        end = max(end, max(existing_by_day) + timedelta(days=1))
        _progress(
            f"bootstrapping persistent title-usage store from full history "
            f"({start.isoformat()}..{end.isoformat()}, one-time)"
        )

    reprocessed_days = set()
    d = start
    while d < end:
        reprocessed_days.add(d)
        d += timedelta(days=1)
    # Drop every day this run is about to (re)compute — including ones that
    # turn out to have no qualifying activity below — so a day that legitimately
    # went from "had rows" to "empty" on reprocessing doesn't keep a stale row.
    existing_by_day = {day: row for day, row in existing_by_day.items() if day not in reprocessed_days}

    by_day: dict[date, dict[str, Any]] = {}
    with _TitleUsageStore(facts_path) as title_usage:
        title_usage.reset_window(start, end)
        cursor = start
        processed_days = 0
        recent_start = end - timedelta(days=RECENT_WINDOW_DAYS)
        while cursor < end:
            if cursor < recent_start:
                chunk_end = min(cursor + timedelta(days=DEFAULT_CHUNK_DAYS), recent_start)
            else:
                chunk_end = min(cursor + timedelta(days=RECENT_CHUNK_DAYS), end)
            span_start, span_end = map(as_local, date_to_dt_range(cursor, chunk_end - timedelta(days=1)))
            for span in focus_spans(start=span_start, end=span_end, enrich_polylogue=False, ensure=False):
                if span.kind != "focused" or not span.title or not span.app or span.duration_s <= 0:
                    continue
                normalized = normalize_title(span.app, span.title)
                key = hash_title(span.app, normalized)
                classification = classifications.get(key)
                if classification is None:
                    # Fallback: in-Lynchpin rules layer. Covers kitty + project-slug,
                    # Claude Code session-prefix sentinels, and topic slugs.
                    classification = classify_title_via_rules(span.app, span.title, normalized)
                title_usage.ensure_row(
                    title_hash=key,
                    app=span.app,
                    normalized_title=normalized,
                    example_title=span.title,
                    classification=classification,
                )
                for day, segment in split_by_day(span.start, span.end):
                    if day < start or day >= end:
                        continue
                    seconds = duration_s(segment)
                    if seconds <= 0:
                        continue
                    title_usage.add(title_hash=key, app=span.app, day=day, seconds=seconds)
                    day_row = by_day.setdefault(day, _empty_day(day))
                    day_row["focused_seconds"] += seconds
                    if classification is None:
                        continue
                    day_row["matched_seconds"] += seconds
                    source = classification.classification_source or "unknown"
                    day_row["source_counts"][source] += 1
                    if source == "gpt":
                        day_row["gpt_matched_seconds"] += seconds
                    _add_bucket(day_row["activity_seconds"], classification.activity, seconds)
                    _add_bucket(day_row["content_type_seconds"], classification.content_type, seconds)
                    _add_bucket(day_row["attention_seconds"], classification.attention_level, seconds)
                    _add_bucket(day_row["topic_seconds"], classification.topic_category, seconds)
                    _add_bucket(day_row["platform_seconds"], classification.platform, seconds)
            cursor = chunk_end
            processed_days += (chunk_end - span_start.date()).days
            title_usage.commit()
            _progress(f"processed {processed_days} day(s) through {chunk_end.isoformat()}")

        for day_row in by_day.values():
            _finish_day(day_row)

        merged_by_day = {**existing_by_day, **by_day}

        atomic_write_ndjson(output, (merged_by_day[day] for day in sorted(merged_by_day)))

        def usage_rows() -> Iterator[dict[str, Any]]:
            for row in title_usage.iter_rows():
                row["focused_seconds"] = round(float(row["focused_seconds"]), 3)
                yield row

        usage_payloads = list(usage_rows())
        atomic_write_ndjson(usage_output, usage_payloads)

        usage_selected: dict[ProductPartitionKey, list[dict[str, Any]]] = {}
        for row in usage_payloads:
            first_date = str(row.get("first_date") or "0000-01")
            month = first_date[:7] if len(first_date) >= 7 else "unknown"
            partition_key = ProductPartitionKey.month("activity_content.title_usage", month)
            usage_selected.setdefault(partition_key, [])
            usage_selected[partition_key].append(row)
        usage_refs: dict[ProductPartitionKey, Any] = {}
        for partition_key, rows in usage_selected.items():
            usage_refs[partition_key] = usage_partition_store.put(
                partition_key, _encode_rows(rows), format="ndjson", input_digest=input_signature,
                row_count=len(rows),
            )
        usage_partition_store.publish(
            usage_refs,
            metadata={"dataset": "lynchpin.activity_content.title_usage", "input_signature": input_signature},
        )

        selected: dict[ProductPartitionKey, Any] = partition_store.logical_partitions()
        if not bounded_request:
            selected = {}
            publish_days = merged_by_day
        else:
            affected = {start + timedelta(days=offset) for offset in range((end - start).days)}
            selected = {
                key: ref for key, ref in selected.items()
                if key.value not in {day.isoformat() for day in affected}
            }
            publish_days = by_day
        for day, row in publish_days.items():
            partition_key = ProductPartitionKey.day("activity_content.daily", day)
            selected[partition_key] = partition_store.put(
                partition_key, _encode_row(row), format="ndjson", input_digest=input_signature,
                row_count=1, first_date=day, last_date=day,
            )
        partition_store.publish(
            selected,
            metadata={"dataset": "lynchpin.activity_content_daily", "input_signature": input_signature},
        )

        title_usage_count = title_usage.count()
        unmatched_title_count = title_usage.unmatched_count()
        top_unmatched_titles = title_usage.top_unmatched()

        # Manifest totals describe the whole product (matching first_date/last_date
        # below), not just the window this call reprocessed — summed from the
        # merged day-rows rather than tracked incrementally during the loop.
        focused_seconds_total = sum(row["focused_seconds"] for row in merged_by_day.values())
        matched_seconds_total = sum(row["matched_seconds"] for row in merged_by_day.values())
        source_counts: Counter[str] = Counter()
        for row in merged_by_day.values():
            source_counts.update(row["source_counts"])

        previous_manifest = _read_manifest(output.with_suffix(".manifest.json"))
        manifest = {
            "dataset": "lynchpin.activity_content_daily",
            "computed_coverage": _computed_coverage(previous_manifest, start, end, acquired_versions),
            "schema_version": ACTIVITY_CONTENT_SCHEMA_VERSION,
            "materialized_path": str(output),
            "title_usage_path": str(usage_output),
            "row_count": len(merged_by_day),
            "title_usage_count": title_usage_count,
            "unmatched_title_count": unmatched_title_count,
            "top_unmatched_titles": top_unmatched_titles,
            "first_date": min(merged_by_day).isoformat() if merged_by_day else None,
            "last_date": max(merged_by_day).isoformat() if merged_by_day else None,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "focused_seconds": round(focused_seconds_total, 3),
            "matched_seconds": round(matched_seconds_total, 3),
            "matched_ratio": round(matched_seconds_total / focused_seconds_total, 6) if focused_seconds_total else 0.0,
            "source_counts": dict(sorted(source_counts.items())),
            "input_versions": acquired_versions,
            "input_files": [str(path) for path in input_files],
            "input_file_count": len(input_files),
            "input_latest_mtime": latest_mtime_iso(input_files),
            "partition_store": str(partition_store.root),
            "partition_scheme": "logical_day",
            "product_paths": {
                key.value: str(partition_store.root / ref.path)
                for key, ref in sorted(selected.items(), key=lambda item: item[0].value)
            },
            "title_usage_partition_store": str(usage_partition_store.root),
        }
        if input_versions(input_files) != acquired_versions:
            raise MaterializationError("activity_content", reason="inputs changed during acquisition; previous generation retained")
        write_manifest(output.with_suffix(".manifest.json"), manifest)
        return manifest


def _input_signature(input_files: tuple[Path, ...]) -> str:
    values: list[tuple[str, int, int]] = []
    for path in input_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        values.append((str(path), stat.st_size, stat.st_mtime_ns))
    return deterministic_input_digest(values)


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _encode_row(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _encode_rows(rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> bytes:
    return b"".join(_encode_row(row) for row in rows)


def _read_partitioned_daily(store: ArtifactStore) -> dict[date, dict[str, Any]]:
    rows: dict[date, dict[str, Any]] = {}
    for key, ref in store.logical_partitions().items():
        if key.product != "activity_content.daily":
            continue
        for line in store.read(ref).decode().splitlines():
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows[date.fromisoformat(str(payload["date"]))] = payload
    return rows


def _migrate_usage_store(store: ArtifactStore, output: Path) -> None:
    if store.manifest_path.exists() or not output.exists():
        return
    facts_path = output.with_name(f"{output.stem}.facts.sqlite3")
    if facts_path.exists():
        # The accumulator owns lifetime facts. A damaged compatibility export
        # must not block migration or force raw-history recomputation.
        usage = _TitleUsageStore(facts_path)
        try:
            rows = list(usage.iter_rows())
        finally:
            usage.close()
    else:
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        first = str(row.get("first_date") or "0000-01")
        by_month.setdefault(first[:7] if len(first) >= 7 else "unknown", []).append(row)
    selected: dict[ProductPartitionKey, Any] = {}
    for month, month_rows in by_month.items():
        partition_key = ProductPartitionKey.month("activity_content.title_usage", month)
        selected[partition_key] = store.put(
            partition_key, _encode_rows(month_rows), format="ndjson",
            row_count=len(month_rows),
        )
    if selected:
        store.publish(selected, metadata={"migration": "legacy-monolith", "validated": True})


def activity_content_input_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in (canonical_activitywatch_events_path(), title_metadata_path())
        if path.exists()
    )


def _progress(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    sys.stderr.write(f"[{stamp}] activity-content: {message}\n")
    sys.stderr.flush()


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not manifest.exists():
        raise FileNotFoundError(
            "canonical ActivityWatch events manifest is missing; run "
            "python -m lynchpin.ingest.activitywatch_materialize first"
        )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    first = start or date.fromisoformat(str(payload["first_date"]))
    last_inclusive = date.fromisoformat(str(payload["last_date"]))
    return first, end or (last_inclusive + timedelta(days=1))


def _read_existing_daily(output: Path) -> dict[date, dict[str, Any]]:
    """Load previously materialized daily rows, keyed by date.

    materialize_activity_content merges this with whatever it (re)computes
    for the requested window, so a narrow request only pays for its own
    window instead of the product's entire history (lynchpin-d36) while
    still producing a complete, correct daily.ndjson.
    """
    if not output.exists():
        return {}
    rows: dict[date, dict[str, Any]] = {}
    with output.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            rows[date.fromisoformat(payload["date"])] = payload
    return rows


def _empty_day(day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "focused_seconds": 0.0,
        "matched_seconds": 0.0,
        "gpt_matched_seconds": 0.0,
        "unmatched_seconds": 0.0,
        "matched_ratio": 0.0,
        "gpt_matched_ratio": 0.0,
        "activity_seconds": defaultdict(float),
        "content_type_seconds": defaultdict(float),
        "attention_seconds": defaultdict(float),
        "topic_seconds": defaultdict(float),
        "platform_seconds": defaultdict(float),
        "source_counts": Counter(),
    }


def _finish_day(row: dict[str, Any]) -> None:
    focused = float(row["focused_seconds"])
    matched = float(row["matched_seconds"])
    gpt_matched = float(row["gpt_matched_seconds"])
    row["unmatched_seconds"] = max(focused - matched, 0.0)
    row["matched_ratio"] = round(matched / focused, 6) if focused else 0.0
    row["gpt_matched_ratio"] = round(gpt_matched / focused, 6) if focused else 0.0
    for key in (
        "focused_seconds",
        "matched_seconds",
        "gpt_matched_seconds",
        "unmatched_seconds",
    ):
        row[key] = round(float(row[key]), 3)
    for key in (
        "activity_seconds",
        "content_type_seconds",
        "attention_seconds",
        "topic_seconds",
        "platform_seconds",
    ):
        row[key] = {bucket: round(value, 3) for bucket, value in sorted(row[key].items()) if value > 0}
    row["source_counts"] = dict(sorted(row["source_counts"].items()))


def _add_bucket(target: dict[str, float], bucket: str | None, seconds: float) -> None:
    if bucket:
        target[bucket] += seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ActivityWatch content rollups")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_activity_content(start=args.start, end=args.end, output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
