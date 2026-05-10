"""Tests for lynchpin.analysis.core.self_analysis dynamic package discovery."""

from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.core import self_analysis


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSubpackages:
    def test_discovers_real_python_subpackages_only(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / "lynchpin"
        _write(root / "core" / "__init__.py")
        _write(root / "analysis" / "__init__.py")
        _write(root / "notes" / "README.md", "not python")
        (root / "__pycache__").mkdir(parents=True)

        monkeypatch.setattr(self_analysis, "_LYNCHPIN_ROOT", root)
        monkeypatch.setattr(self_analysis, "_REPO_ROOT", tmp_path)

        assert self_analysis._subpackages() == ["analysis", "core"]


class TestRunSelfAnalysis:
    def test_import_graph_resolves_absolute_and_relative_imports(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / "lynchpin"
        _write(root / "analysis" / "__init__.py")
        _write(root / "analysis" / "tool.py", "from ..core import cache\n")
        _write(root / "cli.py", "from lynchpin.analysis import tool\n")
        _write(root / "core" / "__init__.py", "from lynchpin.analysis import tool\n")
        _write(root / "core" / "local.py", "from . import helpers\n")

        monkeypatch.setattr(self_analysis, "_LYNCHPIN_ROOT", root)
        monkeypatch.setattr(self_analysis, "_REPO_ROOT", tmp_path)

        edges = {(edge.source, edge.target) for edge in self_analysis._import_graph()}

        assert edges == {("__root__", "analysis"), ("analysis", "core"), ("core", "analysis")}

    def test_run_self_analysis_reports_subpackages_and_import_edges(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / "lynchpin"
        tests_root = tmp_path / "tests"
        _write(root / "__init__.py")
        _write(root / "cli.py", "from lynchpin.analysis import thing\n")
        _write(root / "core" / "__init__.py", "from lynchpin.analysis import thing\n")
        _write(root / "analysis" / "__init__.py", "from ..core import cache\n")
        _write(root / "scripts" / "__init__.py")
        _write(tests_root / "test___init__.py")

        monkeypatch.setattr(self_analysis, "_LYNCHPIN_ROOT", root)
        monkeypatch.setattr(self_analysis, "_REPO_ROOT", tmp_path)

        metrics = self_analysis.run_self_analysis()

        assert {row.subpackage for row in metrics.subpackages} == {"__root__", "analysis", "core", "scripts"}
        assert {edge.source for edge in metrics.import_edges} == {"__root__", "analysis", "core"}
