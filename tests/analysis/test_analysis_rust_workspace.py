"""Tests for analysis.code_index.rust_workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.analysis.code_index.rust_workspace import build_active_rust_graph


def test_rust_graph_requires_project_snapshot(tmp_path: Path) -> None:
    changes = tmp_path / "file_changes.json"
    changes.write_text(json.dumps({"file_changes": []}), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_rust_graph(
            snapshot_file=tmp_path / "missing.json",
            file_changes_file=changes,
        )


def test_rust_graph_requires_file_change_facts(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"projects": []}), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="active file-change facts is missing"):
        build_active_rust_graph(
            snapshot_file=snapshot,
            file_changes_file=tmp_path / "missing.json",
        )


def test_rust_graph_allows_valid_empty_inputs(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"projects": []}), encoding="utf-8")
    changes = tmp_path / "file_changes.json"
    changes.write_text(json.dumps({"file_changes": []}), encoding="utf-8")

    payload = build_active_rust_graph(
        snapshot_file=snapshot,
        file_changes_file=changes,
    )

    assert payload["projects"] == []
