from __future__ import annotations

from pathlib import Path

from lynchpin.scripts.scaffold_browser import (
    SCAFFOLD_ROOT,
    _build_summary,
    _deferred_json_stub,
    _narrative_candidates,
    _read_all_json,
    _split_frontmatter,
)


def test_scaffold_root_points_at_versioned_corpus() -> None:
    assert SCAFFOLD_ROOT.parts[-2:] == ("retrospective", "scaffold")


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


def test_half_narrative_candidates_follow_half_layout() -> None:
    candidates = _narrative_candidates("half", "2026-H1")

    assert candidates
    assert candidates[0].name == "half.md"
    assert candidates[0].parts[-2:] == ("H1", "half.md")


def test_build_summary_marks_stale_narrative_against_newer_scaffold() -> None:
    scaffold = {
        "manifest": {
            "generated_at": "2026-04-23T17:30:42.327820",
            "data_range": {"start": "2013-02-12", "end": "2026-04-23"},
            "sources_available": {"sleep": True, "git": True},
        },
        "narrative_brief": {
            "period": {"start": "2013-02-12", "end": "2026-04-23"},
            "dominant_threads": {"projects": [], "ai_providers": [], "source_coverage": []},
            "analytic_hooks": {"trend_hooks": []},
        },
    }
    narrative = {
        "exists": True,
        "meta": {
            "generated": "2026-03-28",
            "range": "2024-10-14 / 2026-03-28",
        },
    }

    summary = _build_summary("overview", "overview", scaffold, narrative, "Retrospective Overview")

    assert summary["narrative_status"]["state"] == "stale"
    assert "generated_before_scaffold" in summary["narrative_status"]["reasons"]
    assert "range_mismatch" in summary["narrative_status"]["reasons"]


def test_read_all_json_defers_large_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("lynchpin.scripts.scaffold_browser.DEFER_JSON_BYTES", 16)
    small = tmp_path / "manifest.json"
    small.write_text('{"ok": true}')
    large = tmp_path / "ai_activity.json"
    large.write_text('{"payload": "large"}')

    payload = _read_all_json(tmp_path, defer_large=True)

    assert payload["manifest"] == {"ok": True}
    assert payload["ai_activity"] == _deferred_json_stub(large)
