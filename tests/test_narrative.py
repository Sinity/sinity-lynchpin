"""Tests for canonical retrospective narrative file I/O."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lynchpin.retrospective.narrative import (
    Narrative,
    _narrative_hierarchical_path,
    _write_narrative_file,
    load_narratives,
)


class TestWriteNarrativeFile:
    def test_write_uses_canonical_hierarchical_day_path(self, tmp_path):
        narrative = Narrative(
            kind="day",
            key="2026-03-01",
            text="March one.",
            generated_at="2026-03-01T09:00:00Z",
            model="test-model",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        import lynchpin.retrospective.narrative as nar_module

        with patch.object(nar_module, "_NARRATIVE_DIR", tmp_path):
            hier_path = nar_module._narrative_hierarchical_path("day", narrative.key)
            _write_narrative_file(narrative, pass_num=1)

        assert hier_path == Path(tmp_path / "2026" / "H1" / "Q1" / "March" / "1st.md")
        assert hier_path.exists()
        assert not (tmp_path / "days" / "2026-03-01.md").exists()
        assert "March one." in hier_path.read_text(encoding="utf-8")

    def test_write_includes_evidence_bundle_frontmatter(self, tmp_path):
        narrative = Narrative(
            kind="day",
            key="2026-03-16",
            text="Narrative body.",
            generated_at="2026-03-16T12:00:00Z",
            model="test-model",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        import lynchpin.retrospective.narrative as nar_module

        with patch.object(nar_module, "_NARRATIVE_DIR", tmp_path):
            _write_narrative_file(
                narrative,
                pass_num=1,
                evidence_bundle="artefacts/retrospective/narratives/2026/H1/Q1/March/16th.evidence",
            )

        path = _narrative_hierarchical_path("day", "2026-03-16")
        assert path is not None
        contents = (tmp_path / path.relative_to(nar_module._NARRATIVE_DIR)).read_text(encoding="utf-8")
        assert "evidence_bundle:" in contents

    def test_rewrite_tracks_prior_versions_from_same_canonical_file(self, tmp_path):
        narrative = Narrative(
            kind="week",
            key="2026-W11",
            text="Pass one.",
            generated_at="2026-03-16T09:00:00Z",
            model="test-model",
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.1,
        )
        updated = Narrative(
            kind="week",
            key="2026-W11",
            text="Pass two.",
            generated_at="2026-03-16T10:00:00Z",
            model="test-model",
            input_tokens=11,
            output_tokens=12,
            cost_usd=0.2,
        )
        import lynchpin.retrospective.narrative as nar_module

        with patch.object(nar_module, "_NARRATIVE_DIR", tmp_path):
            _write_narrative_file(narrative, pass_num=1, session_id="s1")
            _write_narrative_file(updated, pass_num=2, session_id="s2")
            loaded = load_narratives("week", ["2026-W11"])

        assert loaded["2026-W11"] == "Pass two.\n"
        week_path = tmp_path / "2026" / "H1" / "Q1" / "2026-W11.md"
        contents = week_path.read_text(encoding="utf-8")
        assert "prior_versions:" in contents
        assert "session_id: s1" in contents
