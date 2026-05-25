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


def test_normalize_title_strips_multiple_leading_spinner_chars() -> None:
    """Some terminal title-setters emit multiple spinner chars when the
    spinner overlaps with progress text. Each frame is a distinct char
    in the spinner set; stripping only one leaves the rest as garbage."""
    assert normalize_title("kitty", "⠧⠼⠸ working") == "working"
    assert normalize_title("kitty", "✳ ⠧ task name") == "task name"


def test_normalize_title_strips_progress_counters() -> None:
    """`(3/5)` and `60%` markers churn from frame to frame in agent UIs.
    Stripping collapses N frames into one canonical title."""
    assert normalize_title("kitty", "(3/5) Loading items") == "Loading items"
    assert normalize_title("kitty", "[60%] downloading") == "downloading"
    assert normalize_title("kitty", "Building 12/87 done") == "Building done"


def test_normalize_title_strips_claude_code_interrupt_hint() -> None:
    """Claude Code titles often have trailing '(esc to interrupt · ctrl+t to ...)'
    that flickers in/out as the model thinks."""
    assert normalize_title(
        "kitty", "Working on it (esc to interrupt · ctrl+t to show)"
    ) == "Working on it"


def test_normalize_title_spinner_only_yields_idle_marker() -> None:
    """If the title is ONLY spinner chars (no remaining text), emit the
    canonical idle marker rather than empty string."""
    assert normalize_title("kitty", "⠧⠼⠸") == "claude-code:idle"


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
