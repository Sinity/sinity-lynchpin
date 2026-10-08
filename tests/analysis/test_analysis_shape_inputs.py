"""Tests for active hotspot and guardrail input contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.analysis.interpretation.shape import (
    build_active_guardrails,
    build_active_hotspots,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_hotspots_require_file_changes(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, {"projects": []})

    with pytest.raises(FileNotFoundError, match="active file-change facts is missing"):
        build_active_hotspots(
            file_changes_file=tmp_path / "missing.json",
            snapshot_file=snapshot,
        )


def test_hotspots_require_project_snapshot(tmp_path: Path) -> None:
    changes = tmp_path / "changes.json"
    _write_json(changes, {"file_changes": []})

    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_hotspots(
            file_changes_file=changes,
            snapshot_file=tmp_path / "missing.json",
        )


def test_guardrails_require_file_changes(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, {"projects": []})

    with pytest.raises(FileNotFoundError, match="active file-change facts is missing"):
        build_active_guardrails(
            file_changes_file=tmp_path / "missing.json",
            snapshot_file=snapshot,
        )


def test_guardrails_require_project_snapshot(tmp_path: Path) -> None:
    changes = tmp_path / "changes.json"
    _write_json(changes, {"file_changes": []})

    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_guardrails(
            file_changes_file=changes,
            snapshot_file=tmp_path / "missing.json",
        )
