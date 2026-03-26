"""Tests for pure helper functions in lynchpin/core/projects.py and system/validate.py."""

from __future__ import annotations


from lynchpin.core.projects import _skip_common
from lynchpin.system.validate import _count_iter, _sample_iter


# ---------------------------------------------------------------------------
# _skip_common (core/projects.py)
# ---------------------------------------------------------------------------

class TestSkipCommon:
    def test_git_dir_skipped(self) -> None:
        assert _skip_common(".git/HEAD") is True

    def test_direnv_skipped(self) -> None:
        assert _skip_common(".direnv/env") is True

    def test_node_modules_skipped(self) -> None:
        assert _skip_common("node_modules/lodash/index.js") is True

    def test_target_skipped(self) -> None:
        assert _skip_common("target/debug/binary") is True

    def test_pycache_skipped(self) -> None:
        assert _skip_common("lynchpin/__pycache__/foo.pyc") is True

    def test_artefacts_skipped(self) -> None:
        assert _skip_common("artefacts/lynchpin/cache/data.json") is True

    def test_source_file_not_skipped(self) -> None:
        assert _skip_common("lynchpin/trajectory/day.py") is False

    def test_docs_not_skipped(self) -> None:
        assert _skip_common("docs/plans/roadmap.md") is False

    def test_empty_path_skipped(self) -> None:
        # Empty path has no parts → skips
        assert _skip_common("") is True

    def test_case_insensitive_target(self) -> None:
        assert _skip_common("Target/debug/out") is True


# ---------------------------------------------------------------------------
# _count_iter (system/validate.py)
# ---------------------------------------------------------------------------

class TestCountIter:
    def test_no_limit_counts_all(self) -> None:
        count, truncated = _count_iter(range(10), None)
        assert count == 10
        assert truncated is False

    def test_limit_under_count_truncates(self) -> None:
        count, truncated = _count_iter(range(10), 5)
        assert count == 5
        assert truncated is True

    def test_limit_equal_count_not_truncated(self) -> None:
        count, truncated = _count_iter(range(5), 5)
        assert count == 5
        assert truncated is False

    def test_limit_over_count_not_truncated(self) -> None:
        count, truncated = _count_iter(range(3), 10)
        assert count == 3
        assert truncated is False

    def test_empty_iterable(self) -> None:
        count, truncated = _count_iter([], None)
        assert count == 0
        assert truncated is False

    def test_generator_input(self) -> None:
        count, truncated = _count_iter((x for x in range(7)), 5)
        assert count == 5
        assert truncated is True


# ---------------------------------------------------------------------------
# _sample_iter (system/validate.py)
# ---------------------------------------------------------------------------

class TestSampleIter:
    def test_no_limit_returns_all(self) -> None:
        records, truncated = _sample_iter([1, 2, 3], None)
        assert records == [1, 2, 3]
        assert truncated is False

    def test_limit_under_count_truncates(self) -> None:
        records, truncated = _sample_iter([1, 2, 3, 4, 5], 3)
        assert records == [1, 2, 3]
        assert truncated is True

    def test_limit_equal_count_not_truncated(self) -> None:
        records, truncated = _sample_iter([1, 2, 3], 3)
        assert records == [1, 2, 3]
        assert truncated is False

    def test_empty_iterable_returns_empty(self) -> None:
        records, truncated = _sample_iter([], None)
        assert records == []
        assert truncated is False

    def test_generator_input(self) -> None:
        records, truncated = _sample_iter((x for x in "abcdef"), 4)
        assert records == ["a", "b", "c", "d"]
        assert truncated is True
