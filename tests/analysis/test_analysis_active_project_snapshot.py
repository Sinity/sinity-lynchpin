from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

from lynchpin.analysis.active.git_facts import (
    build_active_commit_facts,
    build_active_file_change_facts,
)
from lynchpin.analysis.active.snapshot import build_active_project_snapshot
from lynchpin.core.projects import ProjectProfile


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = os.environ.copy()
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)


def _classify(path: str) -> str | None:
    if path == "pyproject.toml":
        return "config"
    if path.startswith("tests/"):
        return "tests"
    if path.endswith(".py"):
        return "src"
    return None


def test_active_project_snapshot_uses_default_branch_first_parent(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "pyproject.toml").write_text("[tool.ruff]\n[tool.mypy]\n[tool.pytest.ini_options]\n", encoding="utf-8")
    (repo / "demo.py").write_text("print('demo')\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", "pyproject.toml", "demo.py", "tests/test_demo.py")
    _git(repo, "commit", "-m", "feat: base", when="2026-05-02T10:00:00+00:00")
    _git(repo, "switch", "-c", "side")
    (repo / "side.py").write_text("print('side')\n", encoding="utf-8")
    _git(repo, "add", "side.py")
    _git(repo, "commit", "-m", "feat: side branch", when="2026-05-03T10:00:00+00:00")
    _git(repo, "switch", "master")

    profile = ProjectProfile(
        name="demo",
        path=repo,
        classify=_classify,
        categories=("src", "tests", "config"),
        colors={},
    )
    payload = build_active_project_snapshot(
        start=date(2026, 5, 1),
        end=date(2026, 5, 4),
        projects=("demo",),
        profiles={"demo": profile},
    )

    row = payload["projects"][0]
    assert row["project"] == "demo"
    assert row["default_branch"] == "master"
    assert row["structure"]["tracked_files"] == 3
    assert row["structure"]["categories"]["tests"]["files"] == 1
    assert row["quality_gates"] == ("mypy", "pyproject", "pytest", "ruff", "tests")
    assert row["recent_git"]["commit_count"] == 1
    assert row["recent_git"]["top_subjects"] == ["feat: base"]
    assert row["recent_git"]["conventional_kinds"] == {"feat": 1}


def test_active_git_facts_emit_commit_and_file_rows_without_side_branches(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (repo / "demo.py").write_text("print('demo')\n", encoding="utf-8")
    _git(repo, "add", "pyproject.toml", "demo.py")
    _git(repo, "commit", "-m", "feat(core): base (#5)", when="2026-05-02T10:00:00+00:00")
    (repo / "demo.py").write_text("print('changed')\n", encoding="utf-8")
    _git(repo, "add", "demo.py")
    _git(repo, "commit", "-m", "fix: tune demo", when="2026-05-03T10:00:00+00:00")
    _git(repo, "switch", "-c", "side")
    (repo / "side.py").write_text("print('side')\n", encoding="utf-8")
    _git(repo, "add", "side.py")
    _git(repo, "commit", "-m", "feat: side branch", when="2026-05-04T10:00:00+00:00")
    _git(repo, "switch", "master")

    profile = ProjectProfile(
        name="demo",
        path=repo,
        classify=_classify,
        categories=("src", "tests", "config"),
        colors={},
    )
    commit_payload = build_active_commit_facts(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        projects=("demo",),
        profiles={"demo": profile},
    )
    file_payload = build_active_file_change_facts(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        projects=("demo",),
        profiles={"demo": profile},
    )

    commits = commit_payload["commits"]
    assert [row["subject"] for row in commits] == ["feat(core): base (#5)", "fix: tune demo"]
    assert commits[0]["conventional_kind"] == "feat"
    assert commits[0]["conventional_scope"] == "core"
    assert commits[0]["github_refs"] == {"prs": [5], "issues": []}
    assert commits[0]["categories"] == {"config": 1, "src": 1}
    assert commits[0]["lines_added"] == 2
    assert commits[0]["lines_deleted"] == 0
    assert commits[0]["lines_changed"] == 2
    assert commit_payload["summary"]["commit_count"] == 2

    rows = file_payload["file_changes"]
    assert {row["path"] for row in rows} == {"pyproject.toml", "demo.py"}
    assert {row["change_type"] for row in rows if row["path"] == "demo.py"} == {"added", "modified"}
    modified_demo = [row for row in rows if row["path"] == "demo.py" and row["change_type"] == "modified"][0]
    assert modified_demo["lines_added"] == 1
    assert modified_demo["lines_deleted"] == 1
    assert modified_demo["lines_changed"] == 2
    assert all(row["project"] == "demo" for row in rows)
