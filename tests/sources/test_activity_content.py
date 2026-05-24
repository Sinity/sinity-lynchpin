from __future__ import annotations

import json

from lynchpin.sources.activity_content import iter_activity_content_days, iter_activity_title_usage


def test_activity_content_reader(tmp_path) -> None:
    path = tmp_path / "daily.ndjson"
    path.write_text(
        json.dumps(
            {
                "date": "2026-05-24",
                "focused_seconds": 120.0,
                "matched_seconds": 90.0,
                "gpt_matched_seconds": 60.0,
                "unmatched_seconds": 30.0,
                "matched_ratio": 0.75,
                "gpt_matched_ratio": 0.5,
                "activity_seconds": {"implementation": 90.0},
                "content_type_seconds": {"code": 80.0},
                "attention_seconds": {"deep": 70.0},
                "topic_seconds": {"lynchpin": 60.0},
                "platform_seconds": {"codex": 30.0},
                "source_counts": {"gpt": 1, "rules": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    row = next(iter_activity_content_days(path))

    assert row.date.isoformat() == "2026-05-24"
    assert row.matched_ratio == 0.75
    assert row.activity_seconds == {"implementation": 90.0}
    assert row.source_counts == {"gpt": 1, "rules": 2}


def test_activity_title_usage_reader(tmp_path) -> None:
    path = tmp_path / "title_usage.ndjson"
    path.write_text(
        json.dumps(
            {
                "title_hash": "abc",
                "app": "kitty",
                "normalized_title": "lynchpin",
                "example_title": "✳ lynchpin",
                "focused_seconds": 300.0,
                "span_count": 2,
                "first_date": "2026-05-23",
                "last_date": "2026-05-24",
                "matched": True,
                "classification_source": "gpt",
                "confidence": 0.9,
                "activity": "coding",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    row = next(iter_activity_title_usage(path))

    assert row.title_hash == "abc"
    assert row.matched is True
    assert row.first_date and row.first_date.isoformat() == "2026-05-23"
    assert row.activity == "coding"
