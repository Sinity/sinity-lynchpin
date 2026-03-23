"""Tests for narrative data types and I/O.

Covers: Narrative dataclass, _log_narrative, _write_narrative_file.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from lynchpin.retrospective.narrative import (
    Narrative,
    NarrativeKind,
    _log_narrative,
)


# ---------------------------------------------------------------------------
# _log_narrative
# ---------------------------------------------------------------------------

class TestLogNarrative:
    def test_log_creates_jsonl_entry(self, tmp_path):
        narrative = Narrative(
            kind="week",
            key="2026-W11",
            text="A productive week on sinex.",
            generated_at="2026-03-16T10:00:00Z",
            model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.002,
        )
        import lynchpin.retrospective.narrative as nar_module
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", tmp_path):
            _log_narrative(narrative)

        log_file = tmp_path / "narrative_2026-03-16.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["kind"] == "week"
        assert entry["key"] == "2026-W11"
        assert entry["text"] == "A productive week on sinex."
        assert entry["input_tokens"] == 100

    def test_log_appends_multiple_entries(self, tmp_path):
        n1 = Narrative("day", "2026-03-10", "Day one.", "2026-03-10T09:00:00Z", "m", 10, 5, 0.001)
        n2 = Narrative("day", "2026-03-10", "Day two.", "2026-03-10T10:00:00Z", "m", 12, 6, 0.001)

        import lynchpin.retrospective.narrative as nar_module
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", tmp_path):
            _log_narrative(n1)
            _log_narrative(n2)

        log_file = tmp_path / "narrative_2026-03-10.jsonl"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_log_ioerror_does_not_raise(self, tmp_path):
        """OSError during logging should not propagate -- just log a warning."""
        n = Narrative("week", "2026-W11", "text", "2026-03-16T10:00:00Z", "m", 10, 5, 0.0)
        import lynchpin.retrospective.narrative as nar_module
        # Point to a path that cannot be created (parent is a file)
        fake_dir = tmp_path / "not_a_dir.txt"
        fake_dir.write_text("block")
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", fake_dir / "subdir"):
            _log_narrative(n)  # must not raise
