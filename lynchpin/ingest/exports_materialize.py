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
from ..sources.exports_messenger import iter_fbmessenger_messages, iter_fbmessenger_threads
from ..sources.exports_raindrop import iter_raindrop_bookmarks_all
from ..sources.spotify import iter_streams


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
    return _write_manifest(out.with_suffix(".manifest.json"), "spotify.streaming_history", ordered, product_path=out)


def materialize_reddit() -> dict[str, Any]:
    cfg = get_config()
    source_root = cfg.exports_root / "reddit/processed"
    out_dir = reddit_canonical_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, Any] = {}
    filenames = sorted(
        {
            path.name
            for path in source_root.glob("*/**/*.csv")
            if path.parent.name != "canonical"
        }
    )
    for filename in filenames:
        inputs = sorted(
            path
            for path in source_root.glob(f"*/{filename}")
            if path.parent.name != "canonical"
        )
        rows = _coalesce_csv_rows(inputs)
        output = out_dir / filename
        _write_csv(output, rows)
        reports[filename] = {
            "input_files": len(inputs),
            "row_count": len(rows),
            "path": str(output),
        }
    _write_reddit_manifest(out_dir / "manifest.json", reports, product_path=out_dir)
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
    return _write_manifest(out.with_suffix(".manifest.json"), "raindrop.bookmarks", ordered, product_path=out)


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
    manifest = _write_manifest(out_dir / "manifest.json", "facebook_messenger.messages", messages, product_path=messages_path)
    manifest["thread_count"] = len(threads)
    return manifest


def _spotify_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    for path in sorted(root.iterdir()) if root.exists() else []:
        if not path.is_dir():
            continue
        if (path / "Spotify Account Data").exists() or (path / "Spotify Extended Streaming History").exists():
            roots.append(path)
        elif (path / "legacy").exists():
            roots.append(path / "legacy")
    return roots


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
) -> dict[str, Any]:
    dates = [
        parsed.date()
        for row in rows
        for parsed in [_row_datetime(row)]
        if parsed is not None
    ]
    manifest = {
        "dataset": dataset,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(product_path),
        "row_count": len(rows),
        "first_date": min(dates).isoformat() if dates else None,
        "last_date": max(dates).isoformat() if dates else None,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _write_reddit_manifest(
    path: Path,
    reports: dict[str, Any],
    *,
    product_path: Path,
) -> dict[str, Any]:
    manifest = {
        "dataset": "reddit.canonical_csv",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(product_path),
        "file_count": len(reports),
        "row_count": sum(int(report.get("row_count") or 0) for report in reports.values()),
        "files": reports,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _row_datetime(row: dict[str, Any]) -> datetime | None:
    for key in ("end_time", "created", "timestamp", "date"):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


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
