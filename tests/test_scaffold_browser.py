from __future__ import annotations

from lynchpin.scripts.scaffold_browser import _narrative_candidates, _split_frontmatter


def test_split_frontmatter_extracts_meta_and_body() -> None:
    meta, body = _split_frontmatter(
        "---\nkind: day\nkey: 2026-03-24\n---\n\n# Title\n\nBody text.\n"
    )

    assert meta["kind"] == "day"
    assert str(meta["key"]) == "2026-03-24"
    assert body.startswith("\n# Title")


def test_day_narrative_candidates_match_week_folder_layout() -> None:
    candidates = _narrative_candidates("day", "2026-03-24")

    assert candidates
    assert candidates[0].name == "24th.md"
    assert "W13" in str(candidates[0])
    assert candidates[0].parts[-6:-2] == ("2026", "H1", "Q1", "March")
