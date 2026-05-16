"""Tests for analysis.interpretation.python_dependency_hygiene."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lynchpin.analysis.interpretation.python_dependency_hygiene import (
    _annotate_advisories,
    _external_import_modules,
    _run_audit,
    build_active_python_dependency_hygiene,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_external_import_modules_excludes_internal_and_generated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "")
    _write(
        repo / "pkg" / "core.py",
        """
import requests
import yaml.parser
from pkg import helpers
from pkg.helpers import util
from urllib.parse import urlparse
""".lstrip(),
    )
    _write(repo / "pkg" / "helpers.py", "def util():\n    return 1\n")
    _write(repo / ".lynchpin" / "generated.py", "import boto3\n")

    imports = _external_import_modules(repo, {"pkg", "pkg.core", "pkg.helpers"})

    assert imports == {"requests", "urllib", "yaml"}


def test_annotate_advisories_marks_direct_and_observed_imports() -> None:
    advisories = [
        {"id": "GHSA-demo", "package": "requests"},
        {"id": "GHSA-other", "package": "PyYAML"},
    ]

    annotated = _annotate_advisories(
        advisories,
        direct_deps={"requests", "pyyaml"},
        imported_modules={"requests"},
    )

    assert annotated[0]["direct"] is True
    assert annotated[0]["transitive"] is False
    assert annotated[0]["observed_import"] is True
    assert annotated[1]["direct"] is True
    assert annotated[1]["observed_import"] is False


def test_run_audit_uses_project_path_for_pyproject(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "repo" / "pyproject.toml"
    _write(manifest, "[project]\nname='demo'\ndependencies=[]\n")
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output, text, timeout, check):
        seen["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"dependencies":[]}',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.python_dependency_hygiene.subprocess.run",
        fake_run,
    )

    result = _run_audit(audit_path="/bin/pip-audit", manifest=manifest, kind="pyproject")

    assert result["available"] is True
    assert seen["cmd"] == [
        "/bin/pip-audit",
        "--strict",
        "--format",
        "json",
        "--progress-spinner",
        "off",
        str(manifest.parent),
    ]


def test_run_audit_uses_requirement_flag_for_requirements(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "repo" / "requirements.txt"
    _write(manifest, "requests==2.0.0\n")
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output, text, timeout, check):
        seen["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"dependencies":[]}',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.python_dependency_hygiene.subprocess.run",
        fake_run,
    )

    result = _run_audit(audit_path="/bin/pip-audit", manifest=manifest, kind="requirements")

    assert result["available"] is True
    assert seen["cmd"] == [
        "/bin/pip-audit",
        "--strict",
        "--format",
        "json",
        "--progress-spinner",
        "off",
        "--requirement",
        str(manifest),
    ]


def test_build_records_observed_imports_and_annotates_advisories(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write(
        repo / "pyproject.toml",
        """
[project]
name = "demo"
dependencies = ["requests>=2", "PyYAML"]
""".lstrip(),
    )
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "core.py", "import requests\nfrom pkg import helpers\n")
    _write(repo / "pkg" / "helpers.py", "")

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"projects": [{"project": "demo", "path": str(repo)}]}),
        encoding="utf-8",
    )
    import_graph = tmp_path / "import_graph.json"
    import_graph.write_text(
        json.dumps({
            "projects": [
                {
                    "project": "demo",
                    "modules": [
                        {"name": "pkg"},
                        {"name": "pkg.core"},
                        {"name": "pkg.helpers"},
                    ],
                }
            ]
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.python_dependency_hygiene.shutil.which",
        lambda _binary: "/fake/bin/pip-audit",
    )
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.python_dependency_hygiene._run_audit",
        lambda **_kwargs: {
            "available": True,
            "advisory_count": 2,
            "advisories": [
                {"id": "GHSA-demo", "package": "requests"},
                {"id": "GHSA-transitive", "package": "urllib3"},
            ],
        },
    )

    payload = build_active_python_dependency_hygiene(
        snapshot_file=snapshot,
        import_graph_file=import_graph,
    )

    project = payload["projects"][0]
    assert project["observed_external_import_count"] == 1
    assert project["observed_external_imports"] == ["requests"]
    advisories = project["audit"]["advisories"]
    assert advisories[0]["direct"] is True
    assert advisories[0]["observed_import"] is True
    assert advisories[1]["direct"] is False
    assert advisories[1]["transitive"] is True
    assert advisories[1]["observed_import"] is False
