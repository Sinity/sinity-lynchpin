from __future__ import annotations

import json
from datetime import date, datetime

from lynchpin.sources import wykop
from lynchpin.sources.wykop import date_range


def test_date_range_reads_comment_bounds_and_invalidates(tmp_path) -> None:
    root = tmp_path / "wykop"
    root.mkdir()
    comments = root / "wykop_links_commented.jsonl"
    comments.write_text(
        "\n".join(
            [
                json.dumps({"comment_id": 1, "comment_created_at": "2026-06-03 10:00:00"}),
                json.dumps({"comment_id": 2, "comment_created_at": "2026-06-01 10:00:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert date_range(root=root) == (
        datetime(2026, 6, 1, 10, 0, 0),
        datetime(2026, 6, 3, 10, 0, 0),
    )

    comments.write_text(
        json.dumps({"comment_id": 3, "comment_created_at": "2026-06-05 10:00:00"}) + "\n",
        encoding="utf-8",
    )

    assert date_range(root=root) == (
        datetime(2026, 6, 5, 10, 0, 0),
        datetime(2026, 6, 5, 10, 0, 0),
    )


def test_topic_distribution_passes_date_bounds(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_comments(*, root=None, start=None, end=None):
        calls.append((root, start, end))
        yield wykop.WykopComment(
            kind="link_comment",
            comment_id=1,
            created_at=datetime(2026, 6, 5, 10),
            content="hello",
            rating=0,
            url="https://wykop.pl/link/1",
            parent_id=1,
            parent_title="demo",
            parent_url="https://example.com",
            parent_tags=("python", "lynchpin"),
        )
        yield wykop.WykopComment(
            kind="entry_comment",
            comment_id=2,
            created_at=datetime(2026, 6, 5, 11),
            content="ignored",
            rating=0,
            url="https://wykop.pl/wpis/2",
            parent_id=2,
            parent_title="entry",
            parent_url="https://wykop.pl/wpis/2",
            parent_tags=("ignored",),
        )

    monkeypatch.setattr(wykop, "iter_comments", fake_comments)

    rows = wykop.topic_distribution(
        root=tmp_path,
        start=date(2026, 6, 5),
        end=date(2026, 6, 6),
    )

    assert calls == [(tmp_path, date(2026, 6, 5), date(2026, 6, 6))]
    assert rows == [("python", 1), ("lynchpin", 1)]
