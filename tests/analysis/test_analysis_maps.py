"""Tests for lynchpin.analysis.maps.project_maps pure helper functions."""

from __future__ import annotations

import pytest

from lynchpin.analysis.maps.project_maps import (
    _hotspots_from_commits,
    _is_non_code_module_name,
    _render_markdown,
    _sinex_role,
)


# ---------------------------------------------------------------------------
# role classification
# ---------------------------------------------------------------------------

class TestSinexRole:
    def test_test_directory_returns_test(self) -> None:
        assert _sinex_role("crate/lib/nodes/tests/foo_test.rs") == "test"

    def test_tests_prefix_returns_test(self) -> None:
        assert _sinex_role("tests/integration.rs") == "test"

    def test_test_suffix_returns_test(self) -> None:
        assert _sinex_role("crate/lib/nodes/foo_test.rs") == "test"

    def test_docs_paths_return_docs(self) -> None:
        assert _sinex_role("docs/architecture.md") == "docs"
        assert _sinex_role("CLAUDE.md") == "docs"

    def test_infra_paths_return_infra(self) -> None:
        assert _sinex_role(".github/workflows/ci.yml") == "infra"
        assert _sinex_role("nixos/default.nix") == "infra"
        assert _sinex_role("Cargo.toml") == "infra"
        assert _sinex_role("flake.nix") == "infra"

    def test_crate_source_returns_code(self) -> None:
        assert _sinex_role("crate/lib/nodes/handler.rs") == "code"

    def test_unknown_returns_other(self) -> None:
        assert _sinex_role("some/random/file.txt") == "other"


# ---------------------------------------------------------------------------
# _is_non_code_module_name
# ---------------------------------------------------------------------------

class TestIsNonCodeModuleName:
    def test_docs_prefix(self) -> None:
        assert _is_non_code_module_name("docs") is True
        assert _is_non_code_module_name("docs/api") is True

    def test_test_prefixes(self) -> None:
        assert _is_non_code_module_name("test") is True
        assert _is_non_code_module_name("tests") is True
        assert _is_non_code_module_name("tests/unit") is True

    def test_github_prefix(self) -> None:
        assert _is_non_code_module_name(".github") is True
        assert _is_non_code_module_name(".github/workflows") is True

    def test_config_files(self) -> None:
        assert _is_non_code_module_name("cargo.toml") is True
        assert _is_non_code_module_name("cargo.lock") is True
        assert _is_non_code_module_name("flake.nix") is True
        assert _is_non_code_module_name("justfile") is True
        assert _is_non_code_module_name("readme.md") is True
        assert _is_non_code_module_name("claude.md") is True

    def test_code_modules_not_excluded(self) -> None:
        assert _is_non_code_module_name("src") is False
        assert _is_non_code_module_name("crate/nodes") is False
        assert _is_non_code_module_name("lynchpin") is False
        assert _is_non_code_module_name("ingest") is False

    def test_case_insensitive(self) -> None:
        assert _is_non_code_module_name("DOCS") is True
        assert _is_non_code_module_name("Tests") is True
        assert _is_non_code_module_name("README.md") is True

    def test_scripts_prefix(self) -> None:
        assert _is_non_code_module_name("scripts") is True
        assert _is_non_code_module_name("scripts/deploy.sh") is True

    def test_nixos_prefix(self) -> None:
        assert _is_non_code_module_name("nixos") is True

    def test_empty_string(self) -> None:
        # Empty string doesn't start with any prefix → not non-code
        assert _is_non_code_module_name("") is False


# ---------------------------------------------------------------------------
# _hotspots_from_commits
# ---------------------------------------------------------------------------

def _make_commit(files: list[str], additions: int = 10, lines_changed: int = 20):
    return {"files": files, "additions": additions, "lines_changed": lines_changed}


def _identity_module_key(path: str) -> str:
    """Simple module key: first path segment."""
    return path.split("/")[0] if "/" in path else path


def _identity_role(path: str) -> str:
    """Role: file extension."""
    return path.rsplit(".", 1)[-1] if "." in path else "unknown"


class TestHotspotsFromCommits:
    def test_empty_commits_returns_empty_lists(self) -> None:
        result = _hotspots_from_commits([], _identity_module_key, _identity_role)
        assert result["top_modules"] == []
        assert result["by_role"] == []
        assert result["top_code_modules"] == []

    def test_commit_count_per_module(self) -> None:
        commits = [
            _make_commit(["src/a.rs"]),
            _make_commit(["src/b.rs"]),
            _make_commit(["docs/readme.md"]),
        ]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        by_module = {r["module"]: r for r in result["top_modules"]}
        assert by_module["src"]["commits"] == 2
        assert by_module["docs"]["commits"] == 1

    def test_modules_sorted_by_score_descending(self) -> None:
        # 3 commits to src, 1 to docs → src should rank higher
        commits = [
            _make_commit(["src/a.rs"]),
            _make_commit(["src/b.rs"]),
            _make_commit(["src/c.rs"]),
            _make_commit(["docs/readme.md"]),
        ]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        scores = [r["score"] for r in result["top_modules"]]
        assert scores == sorted(scores, reverse=True)
        assert result["top_modules"][0]["module"] == "src"

    def test_score_formula(self) -> None:
        # Single file, single module: additions=10, lines_changed=20, commits=1, files_touched=1
        # score = 20*0.7 + 1*25 + 1*5 = 14 + 25 + 5 = 44
        commits = [_make_commit(["src/a.rs"], additions=10, lines_changed=20)]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        row = result["top_modules"][0]
        assert row["score"] == pytest.approx(44.0)

    def test_lines_split_across_multiple_modules(self) -> None:
        # Commit touches 2 modules → each gets half the lines_changed
        commits = [_make_commit(["src/a.rs", "docs/readme.md"], lines_changed=40)]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        by_module = {r["module"]: r for r in result["top_modules"]}
        # Each module gets 40/2 = 20 lines_changed
        assert by_module["src"]["lines_changed"] == pytest.approx(20.0)
        assert by_module["docs"]["lines_changed"] == pytest.approx(20.0)

    def test_top_code_modules_excludes_non_code(self) -> None:
        commits = [
            _make_commit(["src/main.rs"]),
            _make_commit(["docs/guide.md"]),
            _make_commit(["tests/unit.rs"]),
        ]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        code_names = {r["module"] for r in result["top_code_modules"]}
        assert "src" in code_names
        assert "docs" not in code_names
        assert "tests" not in code_names

    def test_top_n_limits_output(self) -> None:
        # 5 distinct modules, top_n=2 → only 2 returned
        commits = [_make_commit([f"module{i}/file.rs"]) for i in range(5)]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role, top_n=2)
        assert len(result["top_modules"]) == 2

    def test_commits_without_files_skipped(self) -> None:
        commits = [{"files": [], "additions": 100, "lines_changed": 200}]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        assert result["top_modules"] == []

    def test_role_grouping(self) -> None:
        commits = [
            _make_commit(["src/a.rs"]),
            _make_commit(["src/b.rs"]),
            _make_commit(["src/c.py"]),
        ]
        result = _hotspots_from_commits(commits, _identity_module_key, _identity_role)
        by_role = {r["role"]: r for r in result["by_role"]}
        assert by_role["rs"]["commits"] == 2
        assert by_role["py"]["commits"] == 1


# ---------------------------------------------------------------------------
# _render_markdown
# ---------------------------------------------------------------------------

def _make_module_map() -> dict:
    return {
        "sinex": {
            "module_count": 3,
            "modules": [
                {"module": "crate", "file_count": 20},
            ],
        },
    }


def _make_hotspot_map() -> dict:
    return {
        "sinex": {
            "top_modules": [
                {"module": "crate", "score": 200.0, "commits": 7, "lines_changed": 100.0},
            ],
            "top_code_modules": [
                {"module": "crate", "score": 200.0, "commits": 7, "lines_changed": 100.0},
            ],
            "by_role": [
                {"role": "rs", "score": 200.0, "commits": 7, "lines_changed": 100.0},
            ],
        },
    }


class TestRenderMarkdown:
    def test_returns_string(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert isinstance(result, str)

    def test_contains_h1_header(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "# Project Maps" in result

    def test_contains_sinex_ecosystem(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "## SINEX" in result

    def test_contains_module_count(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "3" in result  # sinex module_count

    def test_contains_module_names(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "crate" in result

    def test_contains_hotspot_scores(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "200.0" in result  # sinex top module score

    def test_no_extra_code_modules_message_when_all_in_top(self) -> None:
        # lynchpin is in both top_modules and top_code_modules → no extras
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "no extra code-only modules" in result

    def test_role_level_section_present(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "Role-level hotspot summary" in result

    def test_role_rs_appears_in_output(self) -> None:
        result = _render_markdown(_make_module_map(), _make_hotspot_map())
        assert "rs" in result
