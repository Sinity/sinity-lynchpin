"""Materialize canonical Google Takeout archive inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..core.cache import input_versions
from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.google_takeout import discover_takeout_archives, is_chrome_history_member, iter_archive_members
from ._manifest import atomic_write_ndjson, write_manifest


GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class _PriorInventory:
    archives: dict[str, dict[str, Any]]
    spans: dict[str, tuple[int, int, int]]


def google_takeout_inventory_dir() -> Path:
    return get_config().accounts_root / "google/processed/takeout-inventory"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _retain_mode(source: Path, replacement: Path) -> None:
    try:
        replacement.chmod(stat.S_IMODE(source.stat().st_mode))
    except FileNotFoundError:
        pass


def _prior_inventory(output_dir: Path) -> _PriorInventory | None:
    manifest_path = output_dir / "manifest.json"
    archives_path = output_dir / "archives.ndjson"
    members_path = output_dir / "members.ndjson"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION:
            return None
        if manifest.get("archives_sha256") != _digest(archives_path):
            return None
        if manifest.get("members_sha256") != _digest(members_path):
            return None
        versions = {item["path"]: item["stat"] for item in manifest["input_versions"]}
        if list(versions) != manifest["input_files"]:
            return None
        archives = {}
        with archives_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                archives[row["path"]] = row
        spans: dict[str, tuple[int, int, int]] = {}
        with members_path.open("rb") as handle:
            while raw_line := handle.readline():
                end = handle.tell()
                row = json.loads(raw_line)
                path = row["archive"]
                if path in spans:
                    start, previous_end, count = spans[path]
                    if previous_end != end - len(raw_line):
                        return None
                    spans[path] = (start, end, count + 1)
                else:
                    spans[path] = (end - len(raw_line), end, 1)
        if set(versions) != set(archives) or set(spans) - set(archives):
            return None
        if len(archives) != manifest["archive_count"]:
            return None
        if sum(row["member_count"] for row in archives.values()) != manifest["member_count"]:
            return None
        counts: Counter[str] = Counter()
        for row in archives.values():
            counts.update(row["product_counts"])
        if dict(sorted(counts.items())) != manifest["product_counts"]:
            return None
        if any(row["member_count"] != spans.get(path, (0, 0, 0))[2] for path, row in archives.items()):
            return None
        for path, row in archives.items():
            row["input_version"] = versions[path]
        return _PriorInventory(archives, spans)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _reused_rows(path: str, members_path: Path, span: tuple[int, int, int] | None) -> Iterator[dict[str, Any]]:
    if span is None:
        return
    start, end, _count = span
    with members_path.open("rb") as handle:
        handle.seek(start)
        while handle.tell() < end:
            row = json.loads(handle.readline())
            if row["archive"] != path:
                raise ValueError("Takeout member cache changed during reuse")
            yield row


def materialize_google_takeout_inventory(*, root: Path | None = None) -> dict[str, Any]:
    output_dir = google_takeout_inventory_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    archives_path = output_dir / "archives.ndjson"
    members_path = output_dir / "members.ndjson"
    manifest_path = output_dir / "manifest.json"

    input_files = google_takeout_input_files(root)
    versions = input_versions(input_files)
    previous = _prior_inventory(output_dir)
    product_counts: Counter[str] = Counter()
    member_count = 0
    archives: list[dict[str, Any]] = []
    reused_archive_count = 0
    scanned_archive_count = 0
    archive_bytes_scanned = 0

    def member_rows() -> Iterator[dict[str, Any]]:
        nonlocal member_count, reused_archive_count, scanned_archive_count, archive_bytes_scanned
        for archive, version in zip(input_files, versions, strict=True):
            path = str(archive)
            prior = previous.archives.get(path) if previous else None
            if previous is not None and version["stat"] is not None and prior is not None and prior["input_version"] == version["stat"]:
                reused_archive_count += 1
                archives.append({key: value for key, value in prior.items() if key != "input_version"})
                product_counts.update(prior["product_counts"])
                member_count += prior["member_count"]
                yield from _reused_rows(path, members_path, previous.spans.get(path))
                continue

            scanned_archive_count += 1
            counts: Counter[str] = Counter()
            archive_members = 0
            total_member_bytes = 0
            chrome_history_members = 0
            errors: list[str] = []
            for member in iter_archive_members(archive, on_error=lambda exc: errors.append(type(exc).__name__)):
                product_counts[member.product] += 1
                counts[member.product] += 1
                member_count += 1
                archive_members += 1
                total_member_bytes += member.size_bytes
                chrome_history_members += is_chrome_history_member(member.path)
                yield {
                    "archive": str(member.archive),
                    "path": member.path,
                    "product": member.product,
                    "size_bytes": member.size_bytes,
                }
            size = archive.stat().st_size
            archive_bytes_scanned += size
            archives.append({
                "path": path,
                "size_bytes": size,
                "member_count": archive_members,
                "total_member_bytes": total_member_bytes,
                "product_counts": dict(sorted(counts.items())),
                "chrome_history_members": chrome_history_members,
                "status": "unreadable" if errors else "ready",
                "error": errors[0] if errors else None,
            })

    with tempfile.TemporaryDirectory(prefix=".takeout-inventory-", dir=output_dir) as staging:
        stage = Path(staging)
        staged_members = stage / "members.ndjson"
        staged_archives = stage / "archives.ndjson"
        atomic_write_ndjson(staged_members, member_rows())
        atomic_write_ndjson(staged_archives, archives)
        if input_versions(input_files) != versions:
            raise RuntimeError("Takeout archive changed during inventory materialization")
        manifest = {
            "dataset": "google.takeout.inventory",
            "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
            "archive_count": len(archives),
            "member_count": member_count,
            "product_counts": dict(sorted(product_counts.items())),
            "archives_path": str(archives_path),
            "members_path": str(members_path),
            "archives_sha256": _digest(staged_archives),
            "members_sha256": _digest(staged_members),
            "reused_archive_count": reused_archive_count,
            "scanned_archive_count": scanned_archive_count,
            "archive_bytes_scanned": archive_bytes_scanned,
            "unreadable_archives": [row["path"] for row in archives if row.get("status") == "unreadable"],
            "input_files": [str(path) for path in input_files],
            "input_file_count": len(input_files),
            "input_latest_mtime": latest_mtime_iso(input_files),
            "input_versions": versions,
        }
        _retain_mode(members_path, staged_members)
        _retain_mode(archives_path, staged_archives)
        os.replace(staged_members, members_path)
        os.replace(staged_archives, archives_path)
        manifest["output_versions"] = input_versions((archives_path, members_path))
        write_manifest(manifest_path, manifest)
    return manifest


def google_takeout_input_files(root: Path | None = None) -> tuple[Path, ...]:
    return tuple(path for path in discover_takeout_archives(root) if path.exists())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize Google Takeout inventory products")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_google_takeout_inventory(root=args.root)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
