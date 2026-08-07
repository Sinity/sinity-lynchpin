from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from lynchpin.ingest.substack_materialize import materialize_substack
from lynchpin.sources.substack import iter_posts, iter_raw_posts


def test_iter_raw_posts_parses_html_metadata_and_publication_alias(tmp_path: Path) -> None:
    root = tmp_path / "substack"
    (root / "INVENTORY.md").parent.mkdir(parents=True)
    (root / "INVENTORY.md").write_text("archive notes", encoding="utf-8")
    (root / "sbstck-dl").mkdir()
    (root / "sbstck-dl/README.md").write_text("downloader", encoding="utf-8")
    path = root / "thingofthings_md/20260102_120000_example-post.html"
    path.parent.mkdir(parents=True)
    path.write_text("<h1>Example title</h1><p>Body</p>", encoding="utf-8")

    post = next(iter_raw_posts(root))

    assert post.publication == "thingofthings"
    assert post.source_publication == "thingofthings_md"
    assert post.slug == "example-post"
    assert post.title == "Example title"
    assert post.published_at is not None
    assert post.published_at.date() == date(2026, 1, 2)
    assert post.content_sha256


def test_materialize_deduplicates_alias_and_prefers_html(tmp_path: Path) -> None:
    root = tmp_path / "substack"
    html = root / "thingofthings/20260102_120000_example.html"
    markdown = root / "thingofthings_md/20260102_120000_example.md"
    html.parent.mkdir(parents=True)
    markdown.parent.mkdir(parents=True)
    html.write_text("<h1>HTML title</h1><p>Body</p>", encoding="utf-8")
    markdown.write_text("# Markdown title\n\nBody", encoding="utf-8")
    output = tmp_path / "derived/posts.ndjson"

    manifest = materialize_substack(root=root, output=output)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert manifest["row_count"] == 1
    assert manifest["duplicate_file_count"] == 1
    assert rows[0]["format"] == "html"
    assert rows[0]["source_publication"] == "thingofthings"


def test_iter_posts_reads_canonical_rows_and_filters_logical_dates(tmp_path: Path) -> None:
    root = tmp_path / "substack"
    post = root / "acx/20260102_120000_example.html"
    post.parent.mkdir(parents=True)
    post.write_text("<h1>Example</h1><p>Body</p>", encoding="utf-8")
    output = tmp_path / "derived/posts.ndjson"
    materialize_substack(root=root, output=output)

    rows = list(iter_posts(output, start=date(2026, 1, 2), end=date(2026, 1, 3), ensure=False))

    assert len(rows) == 1
    assert rows[0].slug == "example"
    assert list(iter_posts(output, start=date(2026, 1, 3), ensure=False)) == []
