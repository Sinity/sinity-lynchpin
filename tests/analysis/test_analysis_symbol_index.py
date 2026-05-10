"""Tests for analysis.code_index.symbol_index."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")
pytest.importorskip("tree_sitter_rust")

from lynchpin.analysis.code_index.symbol_index import (  # noqa: E402
    build_active_symbol_index,
    _load_parsers,
    _walk_python,
    _walk_rust,
)


def _ts_parse(language: str, source: bytes):
    import tree_sitter

    if language == "python":
        import tree_sitter_python as grammar
    else:
        import tree_sitter_rust as grammar
    parser = tree_sitter.Parser()
    parser.language = tree_sitter.Language(grammar.language())
    return parser.parse(source).root_node


def test_python_walk_extracts_function_class_method() -> None:
    src = b"""
def top():
    pass

class Bar:
    def method(self):
        pass

    def _private(self):
        pass
"""
    rows = list(_walk_python(_ts_parse("python", src), project="demo", path="x.py", parents=()))
    by_kind = {(r.symbol_kind, r.qualified_name): r for r in rows}
    assert ("function", "top") in by_kind
    assert ("class", "Bar") in by_kind
    assert ("function", "Bar.method") in by_kind
    assert by_kind[("function", "Bar.method")].parent == "Bar"
    assert by_kind[("function", "Bar._private")].exported is False
    assert by_kind[("function", "top")].exported is True


def test_rust_walk_extracts_struct_fn_impl_with_visibility() -> None:
    src = b"""
pub struct Engine;

struct Hidden;

pub fn run() {}

fn helper() {}

impl Engine {
    pub fn start(&self) {}
    fn internal(&self) {}
}
"""
    rows = list(_walk_rust(_ts_parse("rust", src), project="demo", path="lib.rs", parents=()))
    by_kn = {(r.symbol_kind, r.qualified_name): r for r in rows}
    assert ("struct", "Engine") in by_kn
    assert by_kn[("struct", "Engine")].exported is True
    assert by_kn[("struct", "Hidden")].exported is False
    assert by_kn[("function", "run")].exported is True
    assert by_kn[("function", "helper")].exported is False
    # impl-nested symbols carry the impl's identifier as parent
    assert ("function", "Engine::start") in by_kn
    assert by_kn[("function", "Engine::start")].parent == "Engine"
    assert by_kn[("function", "Engine::start")].exported is True
    assert by_kn[("function", "Engine::internal")].exported is False


def test_load_parsers_returns_python_and_rust() -> None:
    parsers = _load_parsers(("python", "rust"))
    assert "python" in parsers
    assert "rust" in parsers


def test_load_parsers_skips_unknown_language() -> None:
    parsers = _load_parsers(("python", "klingon"))
    assert "python" in parsers
    assert "klingon" not in parsers


def test_build_active_symbol_index_processes_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "module.py").write_text("def hello():\n    pass\nclass Greeter:\n    pass\n")
    (repo / "lib.rs").write_text("pub fn run() {}\nstruct Internal;\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    from lynchpin.core.projects import ProjectProfile

    profiles = {
        "demo": ProjectProfile(
            name="demo",
            path=repo,
            classify=lambda p: "src",
            categories=("src",),
            colors={"src": "#000"},
        )
    }
    payload = build_active_symbol_index(projects=("demo",), profiles=profiles)
    assert payload["projects"][0]["project"] == "demo"
    assert payload["projects"][0]["exists"] is True
    assert payload["projects"][0]["symbol_count"] >= 4
    assert set(payload["projects"][0]["languages"]) == {"python", "rust"}
    qualified = {s["qualified_name"] for s in payload["projects"][0]["symbols"]}
    assert "hello" in qualified
    assert "Greeter" in qualified
    assert "run" in qualified
    assert "Internal" in qualified
