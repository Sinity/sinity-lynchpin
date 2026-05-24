"""Tests for active_code_inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.analysis.code_index.code_inventory import build_active_code_inventory


def test_code_inventory_requires_project_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_code_inventory(snapshot_file=tmp_path / "missing.json")


def test_code_inventory_accepts_empty_snapshot(tmp_path: Path, monkeypatch) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"projects": []}), encoding="utf-8")
    monkeypatch.setattr(
        "lynchpin.analysis.code_index.code_inventory._tokei_version",
        lambda: None,
    )

    payload = build_active_code_inventory(snapshot_file=snapshot)

    assert payload["projects"] == []
