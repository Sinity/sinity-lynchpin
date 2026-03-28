"""Tests for sources/git.py — commit parsing, daily activity, burst detection."""

from datetime import date, datetime, timedelta
from lynchpin.sources.git import (
    _parse_prefix, _count_bursts, _path_root, _parse_date, _parse_git_shortstat,
    GitCommit, GitCommitFact, GitDayActivity, CommitSession,
)


class TestParsePrefix:
    def test_feat(self):
        assert _parse_prefix("feat: add new thing") == "feat"

    def test_fix_parens(self):
        assert _parse_prefix("fix(core): handle null") == "fix"

    def test_unknown(self):
        assert _parse_prefix("random commit message") == "other"

    def test_refactor(self):
        assert _parse_prefix("refactor: split module") == "refactor"


class TestCountBursts:
    def test_no_burst(self):
        ts = [datetime(2026, 3, 15, 10, i * 10) for i in range(3)]
        assert _count_bursts(ts) == 0  # 10min apart

    def test_burst(self):
        base = datetime(2026, 3, 15, 10, 0, 0)
        ts = [base + timedelta(seconds=i * 30) for i in range(5)]  # 0s, 30s, 60s, 90s, 120s apart
        assert _count_bursts(ts) >= 1

    def test_too_few(self):
        assert _count_bursts([datetime(2026, 3, 15, 10)]) == 0


class TestPathRoot:
    def test_src_module(self):
        assert _path_root("src/networking/mod.rs") == "networking"

    def test_top_level(self):
        assert _path_root("Cargo.toml") == "Cargo.toml"

    def test_tests(self):
        assert _path_root("tests/integration/test_api.rs") == "integration"


class TestParseDate:
    def test_iso_date(self):
        assert _parse_date("2026-03-15") == date(2026, 3, 15)

    def test_iso_datetime(self):
        assert _parse_date("2026-03-15T10:00:00+01:00") == date(2026, 3, 15)

    def test_none(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None


class TestParseShortstat:
    def test_full(self):
        result = _parse_git_shortstat(" 3 files changed, 42 insertions(+), 10 deletions(-)")
        assert result["files_changed"] == 3
        assert result["lines_added"] == 42
        assert result["lines_deleted"] == 10

    def test_insertions_only(self):
        result = _parse_git_shortstat(" 1 file changed, 5 insertions(+)")
        assert result["files_changed"] == 1
        assert result["lines_added"] == 5
        assert result["lines_deleted"] == 0
