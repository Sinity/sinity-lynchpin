"""Tracker-note context keeps owner references and observed availability."""

import json
from datetime import datetime, timezone

from lynchpin.sources.chisel_excerpts import collect_excerpts
from lynchpin.sources.chisel_options import BuildOptions


def test_context_keeps_complete_tracker_note_and_reference(tmp_path):
    package = tmp_path / "example"
    tracker = package / "trackers/beads-export.jsonl"
    tracker.parent.mkdir(parents=True)
    text = "A complete note. " * 2000
    tracker.write_text(json.dumps({
        "id": "task-1", "notes": text,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }) + "\n")
    report = collect_excerpts(package, BuildOptions())
    row = json.loads((package / "context/excerpts.jsonl").read_text())
    assert row["text"] == text
    assert row["owner"] == "beads"
    assert row["reference"] == "trackers/beads-export.jsonl:1"
    assert row["task_ref"] == "task-1"
    assert row["timestamp_scope"] == "task update; note creation time unknown"
    assert report["excerpts"] == 1
    assert report["bytes"] == (package / "context/excerpts.jsonl").stat().st_size


def test_context_distinguishes_missing_tracker_from_observed_empty_export(tmp_path):
    package = tmp_path / "example"
    package.mkdir()
    missing = collect_excerpts(package, BuildOptions())
    assert missing["coverage"] == "unavailable"
    tracker = package / "trackers/beads-export.jsonl"
    tracker.parent.mkdir()
    tracker.write_text("")
    observed = collect_excerpts(package, BuildOptions())
    assert observed["coverage"] == "empty_tracker_selection"
    assert observed["excerpts"] == 0
