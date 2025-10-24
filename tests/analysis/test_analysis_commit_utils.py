"""Tests for commit_stats.py and commit_facts.py pure utility functions."""

from __future__ import annotations

import pytest

from lynchpin.analysis.core.naming import safe_key
from lynchpin.analysis.core.commit_stats import _path_component, parse_iso_datetime
from lynchpin.analysis.core.commit_facts import (
    _family_partitions,
    _make_family_manifest,
    _month_key,
    _normalize_commit,
    _primary_path_root,
)


# ---------------------------------------------------------------------------
# parse_iso_datetime (commit_stats)
# ---------------------------------------------------------------------------

class TestParseIsoDt:
    def test_valid_iso_string(self) -> None:
        result = parse_iso_datetime("2026-03-17T10:00:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_z_suffix_handled(self) -> None:
        result = parse_iso_datetime("2026-03-17T10:00:00Z")
        assert result is not None
        assert result.year == 2026

    def test_invalid_string_returns_none(self) -> None:
        assert parse_iso_datetime("not-a-date") is None

    def test_empty_string_returns_none(self) -> None:
        # fromisoformat raises ValueError on empty → returns None
        assert parse_iso_datetime("") is None

    def test_date_only_string(self) -> None:
        result = parse_iso_datetime("2026-03-17")
        assert result is not None
        assert result.day == 17

    def test_date_with_time_and_offset(self) -> None:
        result = parse_iso_datetime("2026-01-15T08:30:00+02:00")
        assert result is not None
        assert result.month == 1


# ---------------------------------------------------------------------------
# _path_component (commit_stats)
# ---------------------------------------------------------------------------

class TestPathComponent:
    def test_empty_string_returns_unknown(self) -> None:
        assert _path_component("") == "unknown"

    def test_none_like_empty_returns_unknown(self) -> None:
        assert _path_component(None) == "unknown"

    def test_crate_returns_third_segment(self) -> None:
        # crate/lib/<name>/file.rs → <name>
        assert _path_component("crate/lib/nodes/foo.rs") == "nodes"

    def test_crate_two_segments_falls_back_to_first(self) -> None:
        # crate/satellites: parts[0]=='crate' but len<3 → falls through to parts[0]
        assert _path_component("crate/satellites") == "crate"

    def test_src_prefix_returns_second_segment(self) -> None:
        assert _path_component("src/main/foo.rs") == "main"

    def test_tests_prefix_returns_second_segment(self) -> None:
        assert _path_component("tests/integration/foo.rs") == "integration"

    def test_source_prefix_returns_second_segment(self) -> None:
        assert _path_component("Source/Module/file.pas") == "Module"

    def test_regular_path_returns_first_segment(self) -> None:
        assert _path_component("docs/readme.md") == "docs"

    def test_windows_separator_normalised(self) -> None:
        assert _path_component("src\\main\\foo.rs") == "main"

    def test_single_segment(self) -> None:
        assert _path_component("main.rs") == "main.rs"


# ---------------------------------------------------------------------------
# _month_key (commit_facts)
# ---------------------------------------------------------------------------

class TestMonthKey:
    def test_iso_timestamp_truncated(self) -> None:
        assert _month_key("2026-03-17T10:00:00") == "2026-03"

    def test_date_string_truncated(self) -> None:
        assert _month_key("2026-01-05") == "2026-01"

    def test_none_returns_unknown_month(self) -> None:
        assert _month_key(None) == "unknown-month"

    def test_empty_string_returns_unknown_month(self) -> None:
        assert _month_key("") == "unknown-month"

    def test_short_string_preserved(self) -> None:
        # If string is shorter than 7 chars, [:7] returns what's there
        assert _month_key("2026") == "2026"


# ---------------------------------------------------------------------------
# safe_key (core.naming)
# ---------------------------------------------------------------------------

class TestSafeKey:
    def test_plain_identifier_unchanged(self) -> None:
        assert safe_key("sinex") == "sinex"

    def test_spaces_replaced_with_underscores(self) -> None:
        result = safe_key("hello world")
        assert " " not in result
        assert "_" in result

    def test_special_chars_replaced(self) -> None:
        result = safe_key("foo/bar-baz")
        assert "/" not in result

    def test_leading_underscores_stripped(self) -> None:
        result = safe_key("  leading spaces")
        assert not result.startswith("_")

    def test_none_returns_unknown(self) -> None:
        assert safe_key(None) == "unknown"

    def test_empty_string_returns_unknown(self) -> None:
        assert safe_key("") == "unknown"

    def test_max_80_chars(self) -> None:
        long_input = "a" * 100
        result = safe_key(long_input)
        assert len(result) <= 80

    def test_dots_preserved(self) -> None:
        # Dot is in [a-zA-Z0-9._-], so should be kept
        result = safe_key("file.name.ext")
        assert "." in result

    def test_hyphen_preserved(self) -> None:
        result = safe_key("my-module")
        assert "-" in result


# ---------------------------------------------------------------------------
# _primary_path_root (commit_facts)
# ---------------------------------------------------------------------------

class TestPrimaryPathRootFacts:
    def test_empty_path_roots_returns_unknown(self) -> None:
        assert _primary_path_root({}) == "unknown"

    def test_none_path_roots_returns_unknown(self) -> None:
        assert _primary_path_root({"path_roots": None}) == "unknown"

    def test_single_root_returned(self) -> None:
        assert _primary_path_root({"path_roots": ["src"]}) == "src"

    def test_alphabetically_first_root_returned(self) -> None:
        assert _primary_path_root({"path_roots": ["src", "crate", "docs"]}) == "crate"


# ---------------------------------------------------------------------------
# _normalize_commit (commit_facts)
# ---------------------------------------------------------------------------

class TestNormalizeCommit:
    def _raw(self, **kwargs):
        defaults = {
            "sha": "abc123",
            "author": "alice",
            "date": "2026-03-17T10:00:00",
            "subject": "fix: something",
            "additions": 10,
            "deletions": 5,
            "lines_changed": 15,
            "files_changed": 2,
            "files": ["src/main.rs", "src/lib.rs"],
            "path_roots": ["src"],
        }
        defaults.update(kwargs)
        return defaults

    def test_commit_sha_mapped(self) -> None:
        result = _normalize_commit(self._raw())
        assert result["commit_sha"] == "abc123"

    def test_author_preserved(self) -> None:
        result = _normalize_commit(self._raw(author="bob"))
        assert result["author"] == "bob"

    def test_paths_sorted(self) -> None:
        raw = self._raw(files=["z/last.rs", "a/first.rs"])
        result = _normalize_commit(raw)
        assert result["paths"] == ["a/first.rs", "z/last.rs"]

    def test_missing_subject_defaults_empty(self) -> None:
        raw = self._raw()
        del raw["subject"]
        result = _normalize_commit(raw)
        assert result["message"] == ""

    def test_missing_additions_defaults_zero(self) -> None:
        raw = self._raw()
        del raw["additions"]
        result = _normalize_commit(raw)
        assert result["additions"] == 0

    def test_path_roots_sorted(self) -> None:
        raw = self._raw(path_roots=["z", "a", "m"])
        result = _normalize_commit(raw)
        assert result["path_roots"] == ["a", "m", "z"]

    def test_files_touched_from_files_changed(self) -> None:
        result = _normalize_commit(self._raw(files_changed=7))
        assert result["files_touched"] == 7

    def test_files_touched_falls_back_to_files_len(self) -> None:
        raw = self._raw(files=["a.rs", "b.rs", "c.rs"])
        del raw["files_changed"]
        result = _normalize_commit(raw)
        assert result["files_touched"] == 3


# ---------------------------------------------------------------------------
# _family_partitions (commit_facts)
# ---------------------------------------------------------------------------

def _commit(ts: str = "2026-03-01", author: str = "alice", path_roots: list[str] | None = None):
    return {
        "timestamp": ts,
        "author": author,
        "path_roots": path_roots or ["src"],
    }


class TestFamilyPartitions:
    def test_empty_commits_returns_empty_dict(self) -> None:
        result = _family_partitions([], "time_month")
        assert result == {}

    def test_time_month_groups_by_year_month(self) -> None:
        commits = [
            _commit("2026-03-01"),
            _commit("2026-03-15"),
            _commit("2026-04-01"),
        ]
        result = _family_partitions(commits, "time_month")
        assert "2026-03" in result
        assert "2026-04" in result
        assert len(result["2026-03"]) == 2
        assert len(result["2026-04"]) == 1

    def test_author_groups_by_author(self) -> None:
        commits = [
            _commit(author="alice"),
            _commit(author="alice"),
            _commit(author="bob"),
        ]
        result = _family_partitions(commits, "author")
        assert len(result["alice"]) == 2
        assert len(result["bob"]) == 1

    def test_primary_path_root_groups_by_first_root(self) -> None:
        commits = [
            _commit(path_roots=["src", "docs"]),
            _commit(path_roots=["crate", "tests"]),
            _commit(path_roots=["docs"]),
        ]
        result = _family_partitions(commits, "primary_path_root")
        # sorted(["src", "docs"])[0] = "docs"; sorted(["crate", "tests"])[0] = "crate"
        assert "docs" in result
        assert "crate" in result

    def test_unknown_family_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported family"):
            _family_partitions([_commit()], "unknown_family")

    def test_commit_without_author_uses_unknown(self) -> None:
        c = {"timestamp": "2026-03-01", "path_roots": ["src"]}
        result = _family_partitions([c], "author")
        assert "unknown" in result


# ---------------------------------------------------------------------------
# _make_family_manifest (commit_facts)
# ---------------------------------------------------------------------------

def _norm_commit(sha: str, ts: str = "2026-03-01T10:00:00", author: str = "alice", path_roots: list[str] | None = None):
    return {
        "commit_sha": sha,
        "timestamp": ts,
        "author": author,
        "path_roots": path_roots or ["src"],
    }


class TestMakeFamilyManifest:
    def test_empty_commits_returns_zero_totals(self) -> None:
        result = _make_family_manifest("sinex", [], "time_month")
        assert result["total_commits"] == 0
        assert result["shard_count"] == 0

    def test_empty_commits_coverage_is_zero(self) -> None:
        result = _make_family_manifest("sinex", [], "time_month")
        assert result["coverage_pct"] == 0.0

    def test_single_commit_produces_one_shard(self) -> None:
        commits = [_norm_commit("abc")]
        result = _make_family_manifest("sinex", commits, "time_month")
        assert result["shard_count"] == 1
        assert result["total_commits"] == 1

    def test_coverage_pct_is_one_for_unique_commits(self) -> None:
        commits = [_norm_commit("abc"), _norm_commit("def")]
        result = _make_family_manifest("sinex", commits, "author")
        assert result["coverage_pct"] == pytest.approx(1.0)

    def test_non_overlapping_true_when_no_duplicates(self) -> None:
        commits = [_norm_commit("abc"), _norm_commit("def")]
        result = _make_family_manifest("sinex", commits, "author")
        assert result["non_overlapping"] is True

    def test_shard_id_format(self) -> None:
        commits = [_norm_commit("abc")]
        result = _make_family_manifest("sinex", commits, "time_month")
        shard_id = result["shards"][0]["shard_id"]
        assert shard_id.startswith("time_month:sinex:")

    def test_shards_sorted_alphabetically_by_key(self) -> None:
        commits = [
            _norm_commit("sha1", ts="2026-03-01", author="alice"),
            _norm_commit("sha2", ts="2026-01-01", author="alice"),
            _norm_commit("sha3", ts="2026-02-01", author="alice"),
        ]
        result = _make_family_manifest("sinex", commits, "time_month")
        keys = [s["key"] for s in result["shards"]]
        assert keys == sorted(keys)

    def test_commits_within_shard_sorted_by_timestamp(self) -> None:
        commits = [
            _norm_commit("sha2", ts="2026-03-15"),
            _norm_commit("sha1", ts="2026-03-01"),
        ]
        result = _make_family_manifest("sinex", commits, "time_month")
        shas = result["shards"][0]["commit_shas"]
        assert shas == ["sha1", "sha2"]

    def test_ecosystem_field_preserved(self) -> None:
        result = _make_family_manifest("polylogue", [_norm_commit("abc")], "author")
        assert result["ecosystem"] == "polylogue"
        assert result["shards"][0]["ecosystem"] == "polylogue"

    def test_two_authors_produce_two_shards(self) -> None:
        commits = [
            _norm_commit("sha1", author="alice"),
            _norm_commit("sha2", author="bob"),
        ]
        result = _make_family_manifest("sinex", commits, "author")
        assert result["shard_count"] == 2
        authors = {s["key"] for s in result["shards"]}
        assert "alice" in authors
        assert "bob" in authors
