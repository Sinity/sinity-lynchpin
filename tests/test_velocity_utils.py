"""Tests for pure helper functions in lynchpin/views/velocity.py."""

from __future__ import annotations

import pytest

from lynchpin.views.velocity import (
    CategoryStats,
    CommitEvent,
    _collapse_commit,
    _skip_common,
)


# ---------------------------------------------------------------------------
# _skip_common (velocity.py version — checks SKIP_EXTENSIONS + SKIP_PATHS)
# ---------------------------------------------------------------------------

class TestSkipCommon:
    def test_lock_file_skipped(self) -> None:
        assert _skip_common("Cargo.lock") is True

    def test_svg_file_skipped(self) -> None:
        assert _skip_common("assets/icon.svg") is True

    def test_png_file_skipped(self) -> None:
        assert _skip_common("logo.png") is True

    def test_map_file_skipped(self) -> None:
        assert _skip_common("bundle.min.map") is True

    def test_woff2_skipped(self) -> None:
        assert _skip_common("font.woff2") is True

    def test_python_file_not_skipped(self) -> None:
        assert _skip_common("src/module.py") is False

    def test_rust_file_not_skipped(self) -> None:
        assert _skip_common("src/main.rs") is False

    def test_artefacts_path_skipped(self) -> None:
        assert _skip_common("artefacts/velocity.html") is True

    def test_data_path_skipped(self) -> None:
        assert _skip_common("data/dump.json") is True

    def test_reports_path_skipped(self) -> None:
        assert _skip_common("reports/summary.md") is True

    def test_src_path_not_skipped(self) -> None:
        assert _skip_common("src/lib.rs") is False

    def test_plain_filename_not_skipped(self) -> None:
        assert _skip_common("README.md") is False

    def test_empty_string_not_skipped(self) -> None:
        # Empty string has no extension match and doesn't start with any skip path
        assert _skip_common("") is False


# ---------------------------------------------------------------------------
# _collapse_commit
# ---------------------------------------------------------------------------

def _make_commit(**overrides) -> CommitEvent:
    defaults = dict(
        hash="abc123def",
        date="2026-03-17",
        author="sinity",
        message="fix: something",
        timestamp="2026-03-17T10:00:00",
        parents=1,
        files_count=3,
        top_files=["src/main.rs", "Cargo.toml"],
    )
    defaults.update(overrides)
    return CommitEvent(**defaults)


class TestCollapseCommit:
    def test_message_prefixed_with_project(self) -> None:
        event = _make_commit(message="fix: test")
        result = _collapse_commit(event, "sinex")
        assert result.message == "[sinex] fix: test"

    def test_top_files_prefixed_with_project(self) -> None:
        event = _make_commit(top_files=["src/a.rs", "src/b.rs"])
        result = _collapse_commit(event, "sinex")
        assert result.top_files == ["sinex:src/a.rs", "sinex:src/b.rs"]

    def test_hash_preserved(self) -> None:
        event = _make_commit(hash="deadbeef")
        result = _collapse_commit(event, "sinex")
        assert result.hash == "deadbeef"

    def test_date_preserved(self) -> None:
        event = _make_commit(date="2026-01-01")
        result = _collapse_commit(event, "sinex")
        assert result.date == "2026-01-01"

    def test_author_preserved(self) -> None:
        event = _make_commit(author="sinity")
        result = _collapse_commit(event, "sinex")
        assert result.author == "sinity"

    def test_result_has_single_category_key(self) -> None:
        event = _make_commit()
        event.by_category = {"src": CategoryStats(added=10, removed=5)}
        result = _collapse_commit(event, "sinex")
        assert list(result.by_category.keys()) == ["sinex"]

    def test_result_added_matches_original(self) -> None:
        event = _make_commit()
        event.by_category = {"src": CategoryStats(added=42, removed=7)}
        result = _collapse_commit(event, "sinex")
        assert result.by_category["sinex"].added == 42

    def test_empty_top_files_yields_empty_list(self) -> None:
        event = _make_commit(top_files=[])
        result = _collapse_commit(event, "lynchpin")
        assert result.top_files == []

    def test_files_count_preserved(self) -> None:
        event = _make_commit(files_count=17)
        result = _collapse_commit(event, "sinex")
        assert result.files_count == 17

    def test_parents_preserved(self) -> None:
        event = _make_commit(parents=2)
        result = _collapse_commit(event, "sinex")
        assert result.parents == 2
