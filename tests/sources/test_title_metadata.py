from __future__ import annotations

import json

from lynchpin.sources.title_metadata import (
    classification_for,
    hash_title,
    iter_title_classifications,
    normalize_title,
)


def test_normalize_title_matches_historical_spinner_key() -> None:
    assert normalize_title("kitty", "✳ sinity-lynchpin") == "sinity-lynchpin"
    assert normalize_title("browser", "Video - Google Chrome") == "Video"
    assert normalize_title("browser", "https://youtube.com/watch?v=abc&list=RDxyz&t=33s") == "https://youtube.com/watch?v=abc"


def test_title_metadata_reader_and_lookup(tmp_path) -> None:
    normalized = normalize_title("chrome", "Example - Google Chrome")
    key = hash_title("chrome", normalized)
    path = tmp_path / "classifications.ndjson"
    path.write_text(
        json.dumps(
            {
                "title_hash": key,
                "app": "chrome",
                "raw_title": "Example - Google Chrome",
                "normalized_title": normalized,
                "activity": "research",
                "topic_category": "systems",
                "classification_source": "gpt",
                "confidence": 0.9,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = list(iter_title_classifications(path))

    assert rows[0].title_hash == key
    assert rows[0].activity == "research"
    assert classification_for("chrome", "Example - Google Chrome", path=path) == rows[0]
