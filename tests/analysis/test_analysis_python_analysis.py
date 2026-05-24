from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.analysis.code_index.python_analysis import (
    build_active_python_complexity,
    build_active_python_import_graph,
)
from lynchpin.analysis.interpretation.python_dependency_hygiene import _internal_module_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_python_import_graph_uses_native_ast_and_internal_module_names(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "from . import core\n")
    _write(repo / "pkg" / "core.py", "from .helpers import util\n")
    _write(repo / "pkg" / "helpers.py", "def util():\n    return 1\n")
    _write(repo / ".lynchpin" / "cache" / "generated.py", "import pkg.core\n")
    _write(repo / "tests" / "test_core.py", "from pkg import core\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project": "demo",
                        "path": str(repo),
                        "dominant_extension": "py",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_active_python_import_graph(snapshot_file=snapshot)

    assert payload["tool_run"] == {"native_ast": {"available": True, "parser": "ast"}}
    project = payload["projects"][0]
    assert project["module_count"] == 4
    assert project["import_edge_count"] == 3
    modules = {row["name"]: row for row in project["modules"]}
    assert modules["pkg"]["imports"] == ["pkg.core"]
    assert modules["pkg.core"]["imports"] == ["pkg.helpers"]
    assert modules["tests.test_core"]["imports"] == ["pkg.core"]


def test_python_dependency_hygiene_consumes_import_graph_module_names(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "core.py", "from . import helpers\n")
    _write(repo / "pkg" / "helpers.py", "")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project": "demo",
                        "path": str(repo),
                        "dominant_extension": "py",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_active_python_import_graph(snapshot_file=snapshot)

    assert _internal_module_index(payload)["demo"] == {"pkg", "pkg.core", "pkg.helpers"}


def test_python_complexity_uses_native_ast_and_ignores_generated_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo / "pkg" / "core.py",
        """
def simple():
    return 1

def branchy(value):
    if value:
        return 1
    for item in range(3):
        if item and value:
            return item
    return 0
""".lstrip(),
    )
    _write(repo / ".lynchpin" / "cache" / "generated.py", "def ignored():\n    return 1\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project": "demo",
                        "path": str(repo),
                        "dominant_extension": "py",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_active_python_complexity(snapshot_file=snapshot)

    assert payload["tool_run"] == {"native_ast": {"available": True, "parser": "ast"}}
    project = payload["projects"][0]
    assert project["file_count"] == 1
    assert project["summary"]["total_functions"] == 2
    assert project["summary"]["rank_distribution"]["A"] == 2
    functions = project["files"][0]["functions"]
    assert [row["name"] for row in functions] == ["simple", "branchy"]
    assert functions[1]["complexity"] > functions[0]["complexity"]


def test_python_import_graph_requires_project_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_python_import_graph(snapshot_file=tmp_path / "missing.json")
