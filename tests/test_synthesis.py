"""Tests for temporal scale utilities and narrative I/O (post-pipeline-pruning)."""
from __future__ import annotations

from pathlib import Path


from lynchpin.retrospective.narrative import NarrativeKind, load_narratives
from lynchpin.retrospective.temporal import (
    SCALE_HIERARCHY,
    child_keys,
    child_scale,
    next_key,
    prior_key,
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

    def test_half_child_is_quarter(self):
        assert child_scale(NarrativeKind.half) is NarrativeKind.quarter

    def test_year_child_is_half(self):
        assert child_scale(NarrativeKind.year) is NarrativeKind.half

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

    def test_half_produces_quarters(self):
        assert child_keys(NarrativeKind.half, "2026-H1") == ["2026-Q1", "2026-Q2"]

    def test_year_produces_halves(self):
        assert child_keys(NarrativeKind.year, "2026") == ["2026-H1", "2026-H2"]

    def test_day_returns_empty(self):
        assert child_keys(NarrativeKind.day, "2026-03-15") == []

    def test_child_keys_month_boundary(self):
        """Months that start/end mid-week should include all overlapping weeks."""
        keys = child_keys(NarrativeKind.month, "2026-02")
        assert len(keys) >= 4  # Feb 2026 spans at least 4 ISO weeks


# ---------------------------------------------------------------------------
# prior_key
# ---------------------------------------------------------------------------


class TestPriorKey:
    def test_week_prior(self):
        assert prior_key(NarrativeKind.week, "2026-W11") == "2026-W10"

    def test_week_prior_year_boundary(self):
        result = prior_key(NarrativeKind.week, "2026-W01")
        assert result is not None
        assert "2025" in result

    def test_month_prior(self):
        assert prior_key(NarrativeKind.month, "2026-03") == "2026-02"

    def test_month_prior_year_boundary(self):
        assert prior_key(NarrativeKind.month, "2026-01") == "2025-12"

    def test_quarter_prior(self):
        assert prior_key(NarrativeKind.quarter, "2026-Q2") == "2026-Q1"

    def test_quarter_prior_year_boundary(self):
        assert prior_key(NarrativeKind.quarter, "2026-Q1") == "2025-Q4"

    def test_half_prior(self):
        assert prior_key(NarrativeKind.half, "2026-H2") == "2026-H1"

    def test_year_prior(self):
        assert prior_key(NarrativeKind.year, "2026") == "2025"

    def test_prior_key_wraps_year_month(self):
        assert prior_key(NarrativeKind.month, "2026-01") == "2025-12"

    def test_prior_key_wraps_year_quarter(self):
        assert prior_key(NarrativeKind.quarter, "2026-Q1") == "2025-Q4"

    def test_day_returns_none(self):
        assert prior_key(NarrativeKind.day, "2026-03-15") == "2026-03-14"


class TestNextKey:
    def test_half_next(self):
        assert next_key(NarrativeKind.half, "2026-H1") == "2026-H2"

    def test_year_next(self):
        assert next_key(NarrativeKind.year, "2026") == "2027"


# ---------------------------------------------------------------------------
# load_narratives
# ---------------------------------------------------------------------------


class TestLoadNarratives:
    def test_loads_from_canonical_hierarchical_file(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.narrative as narr_mod

        monkeypatch.setattr(narr_mod, "_NARRATIVE_DIR", tmp_path / "narratives")

        hier_path = tmp_path / "narratives" / "2026" / "H1" / "Q1" / "March" / "15th.md"
        hier_path.parent.mkdir(parents=True, exist_ok=True)
        hier_path.write_text(
            "---\nkind: day\nkey: 2026-03-15\ngenerated_at: \"2026-03-15T00:00:00Z\"\n---\n\nHIER VERSION",
            encoding="utf-8",
        )

        result = load_narratives("day", ["2026-03-15"])
        assert result["2026-03-15"] == "HIER VERSION"

    def test_missing_file_is_not_returned(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.narrative as narr_mod

        monkeypatch.setattr(narr_mod, "_NARRATIVE_DIR", tmp_path / "narratives")
        assert load_narratives("day", ["2026-03-15"]) == {}

    def test_filters_by_kind(self, tmp_path: Path, monkeypatch):
        import lynchpin.retrospective.narrative as narr_mod

        monkeypatch.setattr(narr_mod, "_NARRATIVE_DIR", tmp_path / "narratives")

        week_path = tmp_path / "narratives" / "2026" / "H1" / "Q1" / "2026-W11.md"
        week_path.parent.mkdir(parents=True, exist_ok=True)
        week_path.write_text(
            "---\nkind: week\nkey: 2026-W11\ngenerated_at: \"2026-03-20T10:00:00Z\"\n---\n\nweek text",
            encoding="utf-8",
        )

        assert load_narratives("day", ["2026-W11"]) == {}
        assert load_narratives("week", ["2026-W11"]) == {"2026-W11": "week text"}


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
            NarrativeKind.half,
            NarrativeKind.year,
        ]

    def test_each_scale_can_produce_children(self):
        """Every non-day scale should produce at least one child key."""
        test_keys = {
            NarrativeKind.week: "2026-W11",
            NarrativeKind.month: "2026-03",
            NarrativeKind.quarter: "2026-Q1",
            NarrativeKind.half: "2026-H1",
            NarrativeKind.year: "2026",
        }
        for scale, key in test_keys.items():
            keys = child_keys(scale, key)
            assert len(keys) > 0, f"{scale.value} should produce children for {key}"
