"""Tests for active_ci_health."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.analysis.frontier.ci_health import build_active_ci_health


def test_ci_health_requires_project_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_ci_health(snapshot_file=tmp_path / "missing.json")


def test_ci_health_reads_workflows_from_snapshot_projects(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - run: pytest
""".lstrip(),
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"projects": [{"project": "demo", "path": str(repo)}]}),
        encoding="utf-8",
    )

    payload = build_active_ci_health(snapshot_file=snapshot)

    assert payload["projects"][0]["project"] == "demo"
    assert payload["projects"][0]["workflow_count"] == 1
    assert payload["projects"][0]["explicit_timeout_count"] == 1
