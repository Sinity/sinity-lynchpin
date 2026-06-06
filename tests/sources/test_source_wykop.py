from __future__ import annotations

import json
from datetime import datetime

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
