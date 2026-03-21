"""Tests for hierarchical narrative synthesis (lynchpin.retrospective.synthesis)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lynchpin.retrospective.narrative import NarrativeKind
from lynchpin.retrospective.synthesis import (
    SCALE_HIERARCHY,
    SynthesisConfig,
    _build_synthesis_prompt,
    _prior_key,
    child_keys,
    child_scale,
    load_narratives,
)


# ---------------------------------------------------------------------------
# child_scale
# ---------------------------------------------------------------------------


class TestChildScale:
    def test_day_has_no_child(self):
        assert child_scale(NarrativeKind.day) is None

    def test_week_child_is_day(self):
        assert child_scale(NarrativeKind.week) is NarrativeKind.day

    def test_month_child_is_week(self):
        assert child_scale(NarrativeKind.month) is NarrativeKind.week

    def test_quarter_child_is_month(self):
        assert child_scale(NarrativeKind.quarter) is NarrativeKind.month

    def test_episode_returns_none(self):
        assert child_scale(NarrativeKind.episode) is None


# ---------------------------------------------------------------------------
# child_keys
# ---------------------------------------------------------------------------


class TestChildKeys:
    def test_week_produces_7_days(self):
        keys = child_keys(NarrativeKind.week, "2026-W11")
        assert len(keys) == 7
        assert keys[0] == "2026-03-09"  # Monday of W11
        assert keys[6] == "2026-03-15"  # Sunday of W11

    def test_month_produces_weeks(self):
        keys = child_keys(NarrativeKind.month, "2026-03")
        assert len(keys) >= 4
        assert all(k.startswith("2026-W") or k.startswith("2025-W") for k in keys)

    def test_quarter_produces_3_months(self):
        keys = child_keys(NarrativeKind.quarter, "2026-Q1")
        assert keys == ["2026-01", "2026-02", "2026-03"]

    def test_quarter_q4(self):
        keys = child_keys(NarrativeKind.quarter, "2026-Q4")
        assert keys == ["2026-10", "2026-11", "2026-12"]

    def test_day_returns_empty(self):
        assert child_keys(NarrativeKind.day, "2026-03-15") == []


# ---------------------------------------------------------------------------
# _prior_key
# ---------------------------------------------------------------------------


class TestPriorKey:
    def test_week_prior(self):
        assert _prior_key(NarrativeKind.week, "2026-W11") == "2026-W10"

    def test_week_prior_year_boundary(self):
        prior = _prior_key(NarrativeKind.week, "2026-W01")
        assert prior is not None
        assert "2025" in prior

    def test_month_prior(self):
        assert _prior_key(NarrativeKind.month, "2026-03") == "2026-02"

    def test_month_prior_year_boundary(self):
        assert _prior_key(NarrativeKind.month, "2026-01") == "2025-12"

    def test_quarter_prior(self):
        assert _prior_key(NarrativeKind.quarter, "2026-Q2") == "2026-Q1"

    def test_quarter_prior_year_boundary(self):
        assert _prior_key(NarrativeKind.quarter, "2026-Q1") == "2025-Q4"

    def test_day_returns_none(self):
        assert _prior_key(NarrativeKind.day, "2026-03-15") is None


# ---------------------------------------------------------------------------
# load_narratives
# ---------------------------------------------------------------------------


class TestLoadNarratives:
    def test_loads_from_jsonl(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.synthesis as synth_mod

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(synth_mod, "_NARRATIVE_LOG_DIR", log_dir)

        log_file = log_dir / "narrative_2026-03-20.jsonl"
        entries = [
            {"kind": "day", "key": "2026-03-15", "generated_at": "2026-03-20T10:00:00Z", "text": "First version."},
            {"kind": "day", "key": "2026-03-15", "generated_at": "2026-03-20T12:00:00Z", "text": "Updated version."},
            {"kind": "day", "key": "2026-03-14", "generated_at": "2026-03-20T10:00:00Z", "text": "Day 14 narrative."},
            {"kind": "week", "key": "2026-W11", "generated_at": "2026-03-20T10:00:00Z", "text": "Week 11."},
        ]
        log_file.write_text(
            "\n".join(json.dumps(e) for e in entries),
            encoding="utf-8",
        )

        # Should get latest version for 2026-03-15
        result = load_narratives("day", ["2026-03-15", "2026-03-14"])
        assert result["2026-03-15"] == "Updated version."
        assert result["2026-03-14"] == "Day 14 narrative."

    def test_missing_keys_not_in_result(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.synthesis as synth_mod

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(synth_mod, "_NARRATIVE_LOG_DIR", log_dir)

        log_file = log_dir / "narrative_2026-03-20.jsonl"
        log_file.write_text(
            json.dumps({"kind": "day", "key": "2026-03-15", "generated_at": "2026-03-20T10:00:00Z", "text": "exists."}),
            encoding="utf-8",
        )

        result = load_narratives("day", ["2026-03-15", "2026-03-16"])
        assert "2026-03-15" in result
        assert "2026-03-16" not in result

    def test_empty_log_dir(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.synthesis as synth_mod

        monkeypatch.setattr(synth_mod, "_NARRATIVE_LOG_DIR", tmp_path / "nonexistent")
        assert load_narratives("day", ["2026-03-15"]) == {}

    def test_filters_by_kind(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.synthesis as synth_mod

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(synth_mod, "_NARRATIVE_LOG_DIR", log_dir)

        log_file = log_dir / "narrative_2026-03-20.jsonl"
        log_file.write_text(
            json.dumps({"kind": "week", "key": "2026-W11", "generated_at": "2026-03-20T10:00:00Z", "text": "week text"}),
            encoding="utf-8",
        )

        # Asking for "day" kind should not return the "week" entry
        assert load_narratives("day", ["2026-W11"]) == {}
        assert load_narratives("week", ["2026-W11"]) == {"2026-W11": "week text"}


# ---------------------------------------------------------------------------
# _build_synthesis_prompt
# ---------------------------------------------------------------------------


class TestBuildSynthesisPrompt:
    def test_includes_child_texts(self):
        children = {
            "2026-03-09": "Day 9 was intense.",
            "2026-03-10": "Day 10 was quiet.",
        }
        prompt = _build_synthesis_prompt(NarrativeKind.week, "2026-W11", children)
        assert "Day 9 was intense." in prompt
        assert "Day 10 was quiet." in prompt
        assert "2026-03-09" in prompt
        assert "2026-03-10" in prompt

    def test_includes_synthesis_instruction(self):
        prompt = _build_synthesis_prompt(
            NarrativeKind.week, "2026-W11", {"2026-03-09": "text"},
        )
        assert "Synthesize" in prompt
        assert "cross-day" in prompt

    def test_empty_children(self):
        prompt = _build_synthesis_prompt(NarrativeKind.week, "2026-W11", {})
        assert "Synthesize" in prompt


# ---------------------------------------------------------------------------
# Scale hierarchy consistency
# ---------------------------------------------------------------------------


class TestScaleHierarchy:
    def test_hierarchy_order(self):
        assert SCALE_HIERARCHY == [
            NarrativeKind.day,
            NarrativeKind.week,
            NarrativeKind.month,
            NarrativeKind.quarter,
        ]

    def test_each_scale_can_produce_children(self):
        """Every non-day scale should produce at least one child key."""
        test_keys = {
            NarrativeKind.week: "2026-W11",
            NarrativeKind.month: "2026-03",
            NarrativeKind.quarter: "2026-Q1",
        }
        for scale, key in test_keys.items():
            keys = child_keys(scale, key)
            assert len(keys) > 0, f"{scale.value} should produce children for {key}"
