from __future__ import annotations

import json
from pathlib import Path

from lynchpin.ingest.substack_materialize import materialize_substack


def test_materialize_substack_writes_atomic_index_and_manifest(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    source = root / "acx/20200101_010203_first-post.md"
    source.parent.mkdir(parents=True)
    source.write_text("# First post\n\nText", encoding="utf-8")
    output = tmp_path / "derived/posts.ndjson"

    manifest = materialize_substack(root=root, output=output)
    manifest_path = output.with_suffix(".manifest.json")

    assert manifest["dataset"] == "substack.posts"
    assert manifest["input_file_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["title"] == "First post"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["row_count"] == 1
    assert not (output.parent / f".{output.name}.tmp").exists()


def test_materialize_substack_empty_root_is_valid_and_rebuildable(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    output = tmp_path / "derived/posts.ndjson"

    manifest = materialize_substack(root=root, output=output)

    assert manifest["row_count"] == 0
    assert manifest["publication_count"] == 0
    assert output.read_text(encoding="utf-8") == ""
