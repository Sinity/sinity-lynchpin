"""Tests for lynchpin.signals pure utility functions."""

from __future__ import annotations

from datetime import datetime, timezone


from lynchpin.signals import (
    _domain_from_url,
    _parse_optional_dt,
    _path_from_window_title,
    _project_hint_from_paths,
    _project_hint_from_text,
    _signal_id,
    _text,
)


# ---------------------------------------------------------------------------
# _text
# ---------------------------------------------------------------------------

class TestText:
    def test_none_returns_none(self) -> None:
        assert _text(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _text("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _text("   ") is None

    def test_plain_string_returned(self) -> None:
        assert _text("hello") == "hello"

    def test_string_stripped(self) -> None:
        assert _text("  hello  ") == "hello"

    def test_integer_coerced_to_string(self) -> None:
        assert _text(42) == "42"

    def test_boolean_coerced_to_string(self) -> None:
        assert _text(True) == "True"

    def test_list_coerced_to_string(self) -> None:
        result = _text([1, 2])
        assert result is not None
        assert "[" in result


# ---------------------------------------------------------------------------
# _domain_from_url
# ---------------------------------------------------------------------------

class TestDomainFromUrl:
    def test_none_returns_none(self) -> None:
        assert _domain_from_url(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _domain_from_url("") is None

    def test_www_prefix_stripped(self) -> None:
        assert _domain_from_url("https://www.github.com/foo") == "github.com"

    def test_no_www_preserved(self) -> None:
        assert _domain_from_url("https://github.com/foo/bar") == "github.com"

    def test_domain_lowercased(self) -> None:
        assert _domain_from_url("https://GitHub.COM/foo") == "github.com"

    def test_port_preserved_in_domain(self) -> None:
        assert _domain_from_url("http://localhost:8080/path") == "localhost:8080"

    def test_path_not_included(self) -> None:
        result = _domain_from_url("https://example.com/some/long/path?q=1")
        assert "/" not in result
        assert result == "example.com"

    def test_string_without_scheme_returns_none(self) -> None:
        # urlparse without scheme → netloc is empty
        result = _domain_from_url("not-a-url")
        assert result is None


# ---------------------------------------------------------------------------
# _path_from_window_title
# ---------------------------------------------------------------------------

class TestPathFromWindowTitle:
    def test_none_returns_none(self) -> None:
        assert _path_from_window_title(None) is None

    def test_no_realm_project_returns_none(self) -> None:
        assert _path_from_window_title("nvim main.rs") is None

    def test_extracts_path_from_title(self) -> None:
        result = _path_from_window_title("nvim /realm/project/sinex/src/main.rs")
        assert result is not None
        assert result.startswith("/realm/project/")

    def test_stops_at_space(self) -> None:
        # Title has a space after the path — should stop there
        result = _path_from_window_title("nvim /realm/project/sinex/src/main.rs (modified)")
        assert result is not None
        assert " " not in result

    def test_trailing_comma_stripped(self) -> None:
        result = _path_from_window_title("nvim /realm/project/sinex/src/main.rs,")
        assert result is not None
        assert not result.endswith(",")

    def test_trailing_colon_stripped(self) -> None:
        result = _path_from_window_title("nvim /realm/project/sinex/src/main.rs:")
        assert result is not None
        assert not result.endswith(":")

    def test_full_path_segment_extracted(self) -> None:
        result = _path_from_window_title("editing /realm/project/sinity-lynchpin/lynchpin/views/warehouse.py")
        assert result == "/realm/project/sinity-lynchpin/lynchpin/views/warehouse.py"


# ---------------------------------------------------------------------------
# _signal_id
# ---------------------------------------------------------------------------

class TestSignalId:
    _t0 = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
    _t1 = datetime(2026, 3, 17, 10, 5, 0, tzinfo=timezone.utc)

    def test_returns_16_hex_chars(self) -> None:
        result = _signal_id("source", self._t0, self._t1)
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_inputs_same_output(self) -> None:
        a = _signal_id("activitywatch.window", self._t0, self._t1, "nvim", "main.rs")
        b = _signal_id("activitywatch.window", self._t0, self._t1, "nvim", "main.rs")
        assert a == b

    def test_different_source_different_output(self) -> None:
        a = _signal_id("source_a", self._t0, self._t1)
        b = _signal_id("source_b", self._t0, self._t1)
        assert a != b

    def test_different_start_different_output(self) -> None:
        a = _signal_id("source", self._t0, self._t1)
        b = _signal_id("source", datetime(2026, 3, 17, 9, 0, 0, tzinfo=timezone.utc), self._t1)
        assert a != b

    def test_none_part_becomes_empty_string(self) -> None:
        # None → "" in join, should not crash
        result = _signal_id("source", self._t0, self._t1, None, "detail")
        assert isinstance(result, str)
        assert len(result) == 16

    def test_extra_parts_affect_output(self) -> None:
        a = _signal_id("src", self._t0, self._t1, "app_a")
        b = _signal_id("src", self._t0, self._t1, "app_b")
        assert a != b


# ---------------------------------------------------------------------------
# _parse_optional_dt
# ---------------------------------------------------------------------------

class TestParseOptionalDt:
    def test_none_returns_none(self) -> None:
        assert _parse_optional_dt(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_optional_dt("") is None

    def test_whitespace_string_returns_none(self) -> None:
        assert _parse_optional_dt("   ") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_optional_dt("not-a-date") is None

    def test_valid_iso_string_returns_datetime(self) -> None:
        result = _parse_optional_dt("2026-03-17T10:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_z_suffix_handled(self) -> None:
        result = _parse_optional_dt("2026-03-17T10:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_datetime_object_returned_with_timezone(self) -> None:
        dt = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
        result = _parse_optional_dt(dt)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_naive_datetime_gets_timezone(self) -> None:
        dt = datetime(2026, 3, 17, 10, 0, 0)  # no tzinfo
        result = _parse_optional_dt(dt)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _project_hint_from_text
# ---------------------------------------------------------------------------

class TestProjectHintFromText:
    def test_none_returns_none(self) -> None:
        assert _project_hint_from_text(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _project_hint_from_text("") is None

    def test_no_project_name_returns_none(self) -> None:
        assert _project_hint_from_text("some random text about nothing specific") is None

    def test_sinex_detected_in_text(self) -> None:
        result = _project_hint_from_text("working on sinex codebase today")
        assert result == "sinex"

    def test_polylogue_detected_in_text(self) -> None:
        result = _project_hint_from_text("polylogue session started")
        assert result == "polylogue"

    def test_case_insensitive_match(self) -> None:
        result = _project_hint_from_text("SINEX BUILD LOG")
        assert result == "sinex"

    def test_longer_name_wins_over_shorter_prefix(self) -> None:
        # "sinity-lynchpin" is longer and contains no overlap with "sinex",
        # but we confirm sinity-lynchpin is matched directly when present
        result = _project_hint_from_text("sinity-lynchpin analysis module")
        assert result == "sinity-lynchpin"


# ---------------------------------------------------------------------------
# _project_hint_from_paths
# ---------------------------------------------------------------------------

class TestProjectHintFromPaths:
    def test_no_args_returns_none(self) -> None:
        assert _project_hint_from_paths() is None

    def test_all_none_returns_none(self) -> None:
        assert _project_hint_from_paths(None, None) is None

    def test_valid_project_path_returns_name(self) -> None:
        result = _project_hint_from_paths("/realm/project/sinex")
        assert result == "sinex"

    def test_nested_project_path_returns_name(self) -> None:
        result = _project_hint_from_paths("/realm/project/sinex/crate/nodes/foo.rs")
        assert result == "sinex"

    def test_first_match_wins(self) -> None:
        result = _project_hint_from_paths(
            "/tmp/not-a-project",
            "/realm/project/sinex",
            "/realm/project/polylogue",
        )
        assert result == "sinex"

    def test_non_project_path_returns_none(self) -> None:
        result = _project_hint_from_paths("/tmp/some/random/path")
        assert result is None

    def test_empty_strings_skipped(self) -> None:
        result = _project_hint_from_paths("", None, "/realm/project/polylogue")
        assert result == "polylogue"
