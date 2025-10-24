"""Tests for lynchpin.analysis.maps.change_surface pure helper functions."""

from __future__ import annotations

import pytest

from lynchpin.analysis.maps.change_surface import (
    _active_months,
    _build_change_surface,
    _primary_root,
    _summary,
)


# ---------------------------------------------------------------------------
# _active_months
# ---------------------------------------------------------------------------

class TestActiveMonths:
    def test_empty_commits_returns_zero(self) -> None:
        assert _active_months([]) == 0

    def test_single_commit_one_month(self) -> None:
        commits = [{"date": "2026-03-01T10:00:00"}]
        assert _active_months(commits) == 1

    def test_two_commits_same_month(self) -> None:
        commits = [
            {"date": "2026-03-01T10:00:00"},
            {"date": "2026-03-15T12:00:00"},
        ]
        assert _active_months(commits) == 1

    def test_two_distinct_months(self) -> None:
        commits = [
            {"date": "2026-02-28T10:00:00"},
            {"date": "2026-03-01T10:00:00"},
        ]
        assert _active_months(commits) == 2

    def test_commit_with_no_date_skipped(self) -> None:
        commits = [{"date": None}, {"date": ""}, {"date": "2026-03-01T00:00:00"}]
        assert _active_months(commits) == 1

    def test_commit_missing_date_key_skipped(self) -> None:
        commits = [{}, {"date": "2026-03-01T00:00:00"}]
        assert _active_months(commits) == 1


# ---------------------------------------------------------------------------
# _primary_root
# ---------------------------------------------------------------------------

class TestPrimaryRoot:
    def test_no_path_roots_key_returns_unknown(self) -> None:
        assert _primary_root({}) == "unknown"

    def test_none_path_roots_returns_unknown(self) -> None:
        assert _primary_root({"path_roots": None}) == "unknown"

    def test_empty_list_returns_unknown(self) -> None:
        assert _primary_root({"path_roots": []}) == "unknown"

    def test_single_root_returned(self) -> None:
        assert _primary_root({"path_roots": ["src"]}) == "src"

    def test_alphabetically_first_root_returned(self) -> None:
        # sorted() → first = 'crate', not 'src'
        assert _primary_root({"path_roots": ["src", "crate", "tools"]}) == "crate"

    def test_single_item_list(self) -> None:
        assert _primary_root({"path_roots": ["docs"]}) == "docs"


# ---------------------------------------------------------------------------
# _build_change_surface
# ---------------------------------------------------------------------------

def _commit(
    sha: str,
    author: str = "alice",
    date: str = "2026-03-01T10:00:00",
    files: list[str] | None = None,
    additions: int = 10,
    lines_changed: int = 20,
    path_roots: list[str] | None = None,
):
    return {
        "sha": sha,
        "author": author,
        "date": date,
        "files": files or [],
        "additions": additions,
        "lines_changed": lines_changed,
        "path_roots": path_roots or (
            [files[0].split("/")[0]] if files else []
        ),
    }


class TestBuildChangeSurface:
    def test_empty_commits_returns_empty_list(self) -> None:
        assert _build_change_surface([], "sinex") == []

    def test_single_commit_produces_one_row(self) -> None:
        commits = [_commit("abc", files=["src/main.rs"], path_roots=["src"])]
        rows = _build_change_surface(commits, "sinex")
        assert len(rows) == 1

    def test_same_key_commits_merged(self) -> None:
        # Same author, month, and primary_root → one row with commit_count=2
        commits = [
            _commit("sha1", author="alice", date="2026-03-01", files=["src/a.rs"], path_roots=["src"]),
            _commit("sha2", author="alice", date="2026-03-15", files=["src/b.rs"], path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert len(rows) == 1
        assert rows[0]["commit_count"] == 2

    def test_different_authors_separate_rows(self) -> None:
        commits = [
            _commit("sha1", author="alice", path_roots=["src"]),
            _commit("sha2", author="bob", path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert len(rows) == 2

    def test_different_months_separate_rows(self) -> None:
        commits = [
            _commit("sha1", date="2026-02-01", path_roots=["src"]),
            _commit("sha2", date="2026-03-01", path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert len(rows) == 2

    def test_additions_and_lines_accumulated(self) -> None:
        commits = [
            _commit("sha1", additions=30, lines_changed=50, path_roots=["src"]),
            _commit("sha2", additions=20, lines_changed=40, path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert rows[0]["additions"] == 50
        assert rows[0]["lines_changed"] == 90

    def test_test_touched_flagged_when_test_file_present(self) -> None:
        commits = [
            _commit("sha1", files=["src/main.rs", "tests/foo.rs"], path_roots=["src"])
        ]
        rows = _build_change_surface(commits, "sinex")
        assert rows[0]["test_touched"] is True

    def test_test_touched_false_when_no_test_files(self) -> None:
        commits = [
            _commit("sha1", files=["src/main.rs", "src/lib.rs"], path_roots=["src"])
        ]
        rows = _build_change_surface(commits, "sinex")
        assert rows[0]["test_touched"] is False

    def test_modules_deduplicated(self) -> None:
        # Two commits to same module → module appears once
        commits = [
            _commit("sha1", files=["crate/nodes/a.rs"], path_roots=["crate"]),
            _commit("sha2", files=["crate/nodes/b.rs"], path_roots=["crate"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        # Both map to 'crate/nodes' via _sinex_module_key
        assert rows[0]["module_count"] == 1

    def test_first_and_last_commit_dates_tracked(self) -> None:
        commits = [
            _commit("sha2", date="2026-03-15T10:00:00", path_roots=["src"]),
            _commit("sha1", date="2026-03-01T10:00:00", path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert rows[0]["first_commit"] == "2026-03-01T10:00:00"
        assert rows[0]["last_commit"] == "2026-03-15T10:00:00"

    def test_rows_sorted_by_commit_count_desc(self) -> None:
        # Three commits to 'src', one to 'docs' → src row first
        commits = [
            _commit("sha1", path_roots=["docs"]),
            _commit("sha2", path_roots=["src"]),
            _commit("sha3", path_roots=["src"]),
            _commit("sha4", path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert rows[0]["primary_path_root"] == "src"

    def test_commit_shas_collected(self) -> None:
        commits = [
            _commit("abc123", path_roots=["src"]),
            _commit("def456", path_roots=["src"]),
        ]
        rows = _build_change_surface(commits, "sinex")
        assert "abc123" in rows[0]["commit_shas"]
        assert "def456" in rows[0]["commit_shas"]

    def test_ecosystem_field_set_correctly(self) -> None:
        commits = [_commit("sha1", path_roots=["src"])]
        rows = _build_change_surface(commits, "polylogue")
        assert rows[0]["ecosystem"] == "polylogue"


# ---------------------------------------------------------------------------
# _summary
# ---------------------------------------------------------------------------

def _make_row(test_touched: bool = False, modules: list[str] | None = None):
    return {
        "test_touched": test_touched,
        "modules": modules or ["src/main"],
    }


class TestSummary:
    def test_empty_rows_returns_zero_counts(self) -> None:
        result = _summary([], [])
        assert result["change_unit_count"] == 0
        assert result["active_months"] == 0
        assert result["test_touched_rate"] == 0.0

    def test_change_unit_count_matches_row_count(self) -> None:
        rows = [_make_row(), _make_row(), _make_row()]
        commits = [{"date": "2026-03-01"}]
        result = _summary(rows, commits)
        assert result["change_unit_count"] == 3

    def test_test_touched_rate_all_true(self) -> None:
        rows = [_make_row(test_touched=True) for _ in range(4)]
        result = _summary(rows, [])
        assert result["test_touched_rate"] == pytest.approx(1.0)

    def test_test_touched_rate_none_true(self) -> None:
        rows = [_make_row(test_touched=False) for _ in range(3)]
        result = _summary(rows, [])
        assert result["test_touched_rate"] == pytest.approx(0.0)

    def test_test_touched_rate_half(self) -> None:
        rows = [_make_row(True), _make_row(False)]
        result = _summary(rows, [])
        assert result["test_touched_rate"] == pytest.approx(0.5)

    def test_change_units_per_active_month_computed(self) -> None:
        # 3 rows, 1 active month → 3.0
        rows = [_make_row() for _ in range(3)]
        commits = [{"date": "2026-03-01"}]
        result = _summary(rows, commits)
        assert result["change_units_per_active_month"] == pytest.approx(3.0)

    def test_active_months_zero_uses_max_one_denominator(self) -> None:
        # No commits → active_months=0, but we shouldn't divide by 0
        rows = [_make_row()]
        result = _summary(rows, [])
        assert result["change_units_per_active_month"] == pytest.approx(1.0)

    def test_top_modules_sorted_descending(self) -> None:
        rows = [
            _make_row(modules=["src/main", "src/lib"]),
            _make_row(modules=["src/main"]),
            _make_row(modules=["docs"]),
        ]
        result = _summary(rows, [])
        top = result["top_modules_by_change_unit_touches"]
        counts = [r["change_unit_count"] for r in top]
        assert counts == sorted(counts, reverse=True)
        assert top[0]["module"] == "src/main"

    def test_top_modules_capped_at_20(self) -> None:
        # 25 distinct modules → only 20 returned
        rows = [_make_row(modules=[f"mod{i}"]) for i in range(25)]
        result = _summary(rows, [])
        assert len(result["top_modules_by_change_unit_touches"]) == 20
