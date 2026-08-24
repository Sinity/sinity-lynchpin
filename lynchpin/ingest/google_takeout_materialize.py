"""Materialize canonical Google Takeout archive inventories."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.google_takeout import archive_inventory, discover_takeout_archives, iter_archive_members
from ._manifest import atomic_write_ndjson, write_manifest


GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION = 1


def google_takeout_inventory_dir() -> Path:
    return get_config().accounts_root / "google/processed/takeout-inventory"


def materialize_google_takeout_inventory(*, root: Path | None = None) -> dict[str, Any]:
    output_dir = google_takeout_inventory_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    archives_path = output_dir / "archives.ndjson"
    members_path = output_dir / "members.ndjson"
    manifest_path = output_dir / "manifest.json"

    archives = archive_inventory(root)
    input_files = google_takeout_input_files(root)
    product_counts: Counter[str] = Counter()
    member_count = 0
    atomic_write_ndjson(
        archives_path,
        (
            {
                "path": str(archive.path),
                "size_bytes": archive.size_bytes,
                "member_count": archive.member_count,
                "total_member_bytes": archive.total_member_bytes,
                "product_counts": dict(archive.product_counts),
                "chrome_history_members": archive.chrome_history_members,
            }
            for archive in archives
        ),
    )

    def member_rows() -> Iterator[dict[str, Any]]:
        nonlocal member_count
        for archive in archives:
            for member in iter_archive_members(archive.path):
                product_counts[member.product] += 1
                member_count += 1
                yield {
                    "archive": str(member.archive),
                    "path": member.path,
                    "product": member.product,
                    "size_bytes": member.size_bytes,
                }

    atomic_write_ndjson(members_path, member_rows())
    manifest = {
        "dataset": "google.takeout.inventory",
        "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
        "archive_count": len(archives),
        "member_count": member_count,
        "product_counts": dict(sorted(product_counts.items())),
        "archives_path": str(archives_path),
        "members_path": str(members_path),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
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
