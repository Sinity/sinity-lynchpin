"""Materialize downloaded Substack posts into a canonical NDJSON index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.substack import (
    SubstackPost,
    format_priority,
    iter_raw_posts,
    substack_input_files,
    substack_manifest_path,
    substack_path,
)
from ._manifest import atomic_write_ndjson, write_manifest

SUBSTACK_SCHEMA_VERSION = 1


def materialize_substack(
    *,
    root: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    raw_root = root or get_config().substack_root
    input_files = substack_input_files(raw_root)
    selected: dict[tuple[str, str], SubstackPost] = {}
    duplicate_count = 0
    for post in iter_raw_posts(raw_root):
        key = (post.publication.casefold(), post.slug.casefold())
        previous = selected.get(key)
        if previous is None:
            selected[key] = post
            continue
        duplicate_count += 1
        if _post_order(post) < _post_order(previous):
            selected[key] = post

    rows = [_row(post) for post in selected.values()]
    rows.sort(key=lambda row: (
        row["published_at"] is None,
        row["published_at"] or "",
        row["publication"],
        row["slug"],
    ))
    target = output or substack_path()
    manifest_target = substack_manifest_path() if output is None else target.with_suffix(".manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_ndjson(target, rows)
    published = [post.published_at for post in selected.values() if post.published_at is not None]
    publications = sorted({post.publication for post in selected.values()})
    manifest = {
        "dataset": "substack.posts",
        "schema_version": SUBSTACK_SCHEMA_VERSION,
        "materialized_path": str(target),
        "raw_root": str(raw_root),
        "row_count": len(rows),
        "publication_count": len(publications),
        "publications": publications,
        "duplicate_file_count": duplicate_count,
        "first_date": min(published).date().isoformat() if published else None,
        "last_date": max(published).date().isoformat() if published else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(manifest_target, manifest)
    return manifest


def _post_order(post: SubstackPost) -> tuple[int, int, str, str]:
    return (format_priority(post.format), len(post.content), str(post.source_path), post.content_sha256)


def _row(post: SubstackPost) -> dict[str, Any]:
    return {
        "publication": post.publication,
        "source_publication": post.source_publication,
        "slug": post.slug,
        "title": post.title,
        "format": post.format,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "source_path": str(post.source_path),
        "content": post.content,
        "content_sha256": post.content_sha256,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description=materialize_substack.__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(materialize_substack(root=args.root, output=args.output), sort_keys=True))


if __name__ == "__main__":
    _main()
