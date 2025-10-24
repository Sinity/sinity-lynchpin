"""Tests for shared analysis path-grouping helpers."""

from __future__ import annotations

from lynchpin.analysis.maps.module_keys import is_test_path, sinex_module_key


class TestSinexModuleKey:
    def test_crate_lib_returns_three_segments(self) -> None:
        assert sinex_module_key("crate/lib/nodes/foo.rs") == "crate/lib/nodes"

    def test_crate_bin_returns_three_segments(self) -> None:
        assert sinex_module_key("crate/bin/server/main.rs") == "crate/bin/server"

    def test_crate_other_returns_two_segments(self) -> None:
        assert sinex_module_key("crate/satellites/foo.rs") == "crate/satellites"

    def test_tests_root_returns_tests(self) -> None:
        assert sinex_module_key("tests/integration.rs") == "tests"

    def test_tools_root_returns_tools(self) -> None:
        assert sinex_module_key("tools/bench.rs") == "tools"

    def test_scripts_root_returns_scripts(self) -> None:
        assert sinex_module_key("scripts/setup.sh") == "scripts"

    def test_unknown_root_returns_first_segment(self) -> None:
        assert sinex_module_key("docs/readme.md") == "docs"

    def test_empty_path_returns_unknown(self) -> None:
        assert sinex_module_key("") == "unknown"

    def test_backslash_normalized(self) -> None:
        assert sinex_module_key(r"crate\lib\nodes\foo.rs") == "crate/lib/nodes"


class TestIsTestPath:
    def test_sinex_embedded_tests_dir(self) -> None:
        assert is_test_path("src/tests/foo.rs", "sinex") is True

    def test_sinex_top_level_tests_dir(self) -> None:
        assert is_test_path("tests/integration.rs", "sinex") is True

    def test_sinex_file_suffix_test_rs(self) -> None:
        assert is_test_path("crate/nodes/foo_test.rs", "sinex") is True

    def test_sinex_regular_source_not_test(self) -> None:
        assert is_test_path("src/main.rs", "sinex") is False

    def test_sinex_windows_path_separator_normalised(self) -> None:
        assert is_test_path(r"src\tests\foo.rs", "sinex") is True

    def test_sinex_case_insensitive(self) -> None:
        assert is_test_path("src/TESTS/foo.rs", "sinex") is True
