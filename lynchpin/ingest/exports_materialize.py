"""Materialize canonical products for export-style datasets."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.exports_messenger import iter_fbmessenger_messages, iter_fbmessenger_threads
from ..sources.exports_raindrop import iter_raindrop_bookmarks_all
from ..sources.spotify import iter_streams
from ._manifest import write_manifest


SPOTIFY_STREAMS_SCHEMA_VERSION = 1
REDDIT_CANONICAL_SCHEMA_VERSION = 1
RAINDROP_BOOKMARKS_SCHEMA_VERSION = 1
MESSENGER_CANONICAL_SCHEMA_VERSION = 1


def spotify_streams_path() -> Path:
    return get_config().exports_root / "spotify/processed/streaming_history.ndjson"


def reddit_canonical_dir() -> Path:
    return get_config().exports_root / "reddit/processed/canonical"


def raindrop_bookmarks_path() -> Path:
    return get_config().exports_root / "raindrop/processed/bookmarks.csv"


def messenger_canonical_dir() -> Path:
    return get_config().exports_root / "comms/facebook-messenger/processed/canonical"


def materialize_all() -> dict[str, Any]:
    return {
        "spotify": materialize_spotify(),
        "reddit": materialize_reddit(),
        "raindrop": materialize_raindrop(),
        "facebook_messenger": materialize_messenger(),
    }


def materialize_spotify() -> dict[str, Any]:
    cfg = get_config()
    roots = _spotify_roots(cfg.exports_root / "spotify/processed")
    out = spotify_streams_path()
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for root in roots:
        for stream in iter_streams(root=root):
            if stream.end_time is None:
                continue
            key = (
                stream.end_time.isoformat(),
                stream.artist,
                stream.track,
                stream.ms_played,
            )
            rows[key] = {
                "end_time": stream.end_time.isoformat(),
                "artist": stream.artist,
                "track": stream.track,
                "ms_played": stream.ms_played,
                "platform": stream.platform,
                "context": stream.context,
                "source_file": stream.source_file,
            }

    ordered = [rows[key] for key in sorted(rows)]
    _write_ndjson(out, ordered)
    source_files = sorted({Path(str(row["source_file"])) for row in ordered if row.get("source_file")})
    return _write_manifest(
        out.with_suffix(".manifest.json"),
        "spotify.streaming_history",
        ordered,
        product_path=out,
        source_files=source_files,
        schema_version=SPOTIFY_STREAMS_SCHEMA_VERSION,
    )


def materialize_reddit() -> dict[str, Any]:
    cfg = get_config()
    source_root = cfg.exports_root / "reddit/processed"
    out_dir = reddit_canonical_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, Any] = {}
    input_roots = _export_roots(source_root)
    filenames = sorted(
        {path.name for root in input_roots for path in root.rglob("*.csv")}
    )
    for filename in filenames:
        inputs = sorted(
            path
            for root in input_roots
            for path in root.rglob(filename)
            if path.is_file()
        )
        rows = _coalesce_csv_rows(inputs)
        first_date, last_date = _row_date_bounds(rows)
        output = out_dir / filename
        _write_csv(output, rows)
        reports[filename] = {
            "first_date": first_date,
            "input_files": len(inputs),
            "input_latest_mtime": latest_mtime_iso(inputs),
            "last_date": last_date,
            "row_count": len(rows),
            "path": str(output),
        }
    _write_reddit_manifest(
        out_dir / "manifest.json",
        reports,
        product_path=out_dir,
        source_files=[path for root in input_roots for path in root.rglob("*.csv") if path.is_file()],
    )
    return {"path": str(out_dir), "files": reports}


def materialize_raindrop() -> dict[str, Any]:
    out = raindrop_bookmarks_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for export, bookmark in iter_raindrop_bookmarks_all():
        key = (str(bookmark.id), bookmark.url)
        rows[key] = {
            "id": str(bookmark.id),
            "title": bookmark.title,
            "note": bookmark.note,
            "excerpt": bookmark.excerpt,
            "url": bookmark.url,
            "folder": bookmark.folder,
            "tags": ",".join(bookmark.tags),
            "created": bookmark.created.isoformat() if bookmark.created else "",
            "cover": bookmark.cover or "",
            "favorite": "true" if bookmark.favorite else "false",
            "source_file": str(export.path),
        }
    ordered = [rows[key] for key in sorted(rows)]
    _write_csv(out, ordered)
    source_files = sorted({Path(str(row["source_file"])) for row in ordered if row.get("source_file")})
    return _write_manifest(
        out.with_suffix(".manifest.json"),
        "raindrop.bookmarks",
        ordered,
        product_path=out,
        source_files=source_files,
        schema_version=RAINDROP_BOOKMARKS_SCHEMA_VERSION,
    )


def materialize_messenger() -> dict[str, Any]:
    cfg = get_config()
    out_dir = messenger_canonical_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = _messenger_thread_files(cfg.fbmessenger_gdpr_root)

    thread_rows: dict[str, dict[str, Any]] = {}
    for thread in iter_fbmessenger_threads(paths=inputs):
        thread_rows[thread.thread_name] = {
            "thread_name": thread.thread_name,
            "participants": thread.participants,
            "source": thread.source,
        }

    message_rows: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for message in iter_fbmessenger_messages(paths=inputs):
        stamp = message.timestamp.isoformat() if message.timestamp else ""
        key = (
            message.thread_name,
            stamp,
            message.sender,
            message.text or "",
            message.kind,
        )
        message_rows[key] = {
            "thread_name": message.thread_name,
            "participants": message.participants,
            "sender": message.sender,
            "timestamp": stamp,
            "text": message.text,
            "kind": message.kind,
            "is_unsent": message.is_unsent,
            "media_count": message.media_count,
            "reaction_count": message.reaction_count,
            "source": message.source,
        }

    threads = [thread_rows[key] for key in sorted(thread_rows)]
    messages = [message_rows[key] for key in sorted(message_rows)]
    threads_path = out_dir / "threads.ndjson"
    messages_path = out_dir / "messages.ndjson"
    _write_ndjson(threads_path, threads)
    _write_ndjson(messages_path, messages)
    manifest = _write_manifest(
        out_dir / "manifest.json",
        "facebook_messenger.messages",
        messages,
        product_path=messages_path,
        source_files=inputs,
        schema_version=MESSENGER_CANONICAL_SCHEMA_VERSION,
        extra={"thread_count": len(threads)},
    )
    return manifest


def _spotify_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    for path in _export_roots(root):
        if (path / "Spotify Account Data").exists() or (path / "Spotify Extended Streaming History").exists():
            roots.append(path)
    return roots


def _export_roots(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in sorted(root.iterdir())
        if path.is_dir() and _is_dated_export_dir(path)
    ]


def _is_dated_export_dir(path: Path) -> bool:
    try:
        datetime.strptime(path.name, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _messenger_thread_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for messages_dir in sorted(root.glob("*/messages")) if root.exists() else []:
        files.extend(
            path for path in sorted(messages_dir.glob("*.json")) if path.is_file()
        )
    return files


def _coalesce_csv_rows(paths: Iterable[Path]) -> list[dict[str, str]]:
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                cleaned = {str(key): str(value or "") for key, value in row.items() if key is not None}
                key = (
                    cleaned.get("id", ""),
                    cleaned.get("permalink", ""),
                    cleaned.get("date", ""),
                )
                if not any(key):
                    key = (path.name, str(len(rows)), "")
                cleaned["source_file"] = str(path)
                rows[key] = cleaned
    return [rows[key] for key in sorted(rows)]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_manifest(
    path: Path,
    dataset: str,
    rows: list[dict[str, Any]],
    *,
    product_path: Path,
    source_files: Iterable[Path] = (),
    schema_version: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_paths = tuple(source_files)
    dates = [
        parsed.date()
        for row in rows
        for parsed in [_row_datetime(row)]
        if parsed is not None
    ]
    manifest = {
        "dataset": dataset,
        "schema_version": schema_version,
        "materialized_path": str(product_path),
        "row_count": len(rows),
        "first_date": min(dates).isoformat() if dates else None,
        "last_date": max(dates).isoformat() if dates else None,
        "input_files": [str(path) for path in source_paths],
        "input_file_count": _path_count(source_paths),
        "input_latest_mtime": latest_mtime_iso(source_paths),
    }
    if extra:
        manifest.update(extra)
    write_manifest(path, manifest)
    return manifest


def _write_reddit_manifest(
    path: Path,
    reports: dict[str, Any],
    *,
    product_path: Path,
    source_files: Iterable[Path] = (),
) -> dict[str, Any]:
    source_paths = tuple(source_files)
    first_date, last_date = _report_date_bounds(reports.values())
    manifest = {
        "dataset": "reddit.canonical_csv",
        "schema_version": REDDIT_CANONICAL_SCHEMA_VERSION,
        "materialized_path": str(product_path),
        "file_count": len(reports),
        "first_date": first_date,
        "last_date": last_date,
        "row_count": sum(int(report.get("row_count") or 0) for report in reports.values()),
        "input_files": [str(path) for path in source_paths],
        "input_file_count": _path_count(source_paths),
        "input_latest_mtime": latest_mtime_iso(source_paths),
        "files": reports,
    }
    write_manifest(path, manifest)
    return manifest


def _row_date_bounds(rows: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [
        parsed.date()
        for row in rows
        for parsed in [_row_datetime(row)]
        if parsed is not None
    ]
    return (
        min(dates).isoformat() if dates else None,
        max(dates).isoformat() if dates else None,
    )


def _report_date_bounds(reports: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    first: str | None = None
    last: str | None = None
    for report in reports:
        report_first = report.get("first_date")
        report_last = report.get("last_date")
        if isinstance(report_first, str) and report_first and (first is None or report_first < first):
            first = report_first
        if isinstance(report_last, str) and report_last and (last is None or report_last > last):
            last = report_last
    return first, last


def _row_datetime(row: dict[str, Any]) -> datetime | None:
    for key in ("end_time", "created", "timestamp", "date"):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _path_count(paths: Iterable[Path]) -> int:
    return sum(1 for path in paths if path.exists())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical export datasets")
    parser.add_argument(
        "dataset",
        nargs="?",
        choices=("all", "spotify", "reddit", "raindrop", "facebook-messenger"),
        default="all",
    )
    args = parser.parse_args(argv)
    if args.dataset == "all":
        report = materialize_all()
    elif args.dataset == "spotify":
        report = materialize_spotify()
    elif args.dataset == "reddit":
        report = materialize_reddit()
    elif args.dataset == "raindrop":
        report = materialize_raindrop()
    else:
        report = materialize_messenger()
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
