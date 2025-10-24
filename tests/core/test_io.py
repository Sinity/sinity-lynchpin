"""Tests for shared analysis IO path resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lynchpin.analysis.core import canonical
from lynchpin.core import io as analysis_io
from lynchpin.core.io import load_json_object


def _fake_config(tmp_path: Path) -> SimpleNamespace:
    repo_root = tmp_path / "repo"
    analysis_output_dir = tmp_path / "knowledge" / "analysis"
    repo_root.mkdir(parents=True)
    analysis_output_dir.mkdir(parents=True)
    return SimpleNamespace(
        repo_root=repo_root,
        analysis_output_dir=analysis_output_dir,
    )


class TestAnalysisIoPaths:
    def test_resolve_repo_path_uses_repo_root_for_relative_paths(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        cfg = _fake_config(tmp_path)
        monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)

        assert analysis_io.resolve_repo_path(
            "lynchpin/analysis/analysis_spec.json"
        ) == str(cfg.repo_root / "lynchpin/analysis/analysis_spec.json")

    def test_resolve_analysis_path_uses_analysis_root_for_relative_paths(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        cfg = _fake_config(tmp_path)
        monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)

        assert analysis_io.resolve_analysis_path("maps/project-maps.md") == str(
            cfg.analysis_output_dir / "maps/project-maps.md"
        )

    def test_absolute_paths_pass_through_unchanged(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        cfg = _fake_config(tmp_path)
        absolute = str(tmp_path / "already" / "absolute.json")
        monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)

        assert analysis_io.resolve_repo_path(absolute) == absolute
        assert analysis_io.resolve_analysis_path(absolute) == absolute

    def test_resolve_artifact_path_is_analysis_root_relative(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        cfg = _fake_config(tmp_path)
        monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)

        spec = {
            "artifacts": {
                "sinex_structure_metrics": "sinex_structure_metrics.json",
            }
        }
        assert analysis_io.resolve_artifact_path(spec, "sinex_structure_metrics") == str(
            cfg.analysis_output_dir / "sinex_structure_metrics.json"
        )


class TestCanonicalSpecLoading:
    def test_load_analysis_spec_resolves_repo_relative_path(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        cfg = _fake_config(tmp_path)
        monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)

        spec_path = cfg.repo_root / "lynchpin" / "analysis" / "analysis_spec.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_payload = {
            "version": "test",
            "artifacts": {"sinex_structure_metrics": "sinex_structure_metrics.json"},
        }
        spec_path.write_text(json.dumps(spec_payload), encoding="utf-8")

        assert (
            canonical.load_analysis_spec("lynchpin/analysis/analysis_spec.json")
            == spec_payload
        )


def test_load_json_object_requires_existing_product(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        load_json_object(tmp_path / "missing.json", label="active project snapshot")


def test_load_json_object_rejects_non_object_payload(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    target.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(ValueError, match="active project snapshot is not a JSON object"):
        load_json_object(target, label="active project snapshot")


def test_load_json_object_returns_dict_payload(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    target.write_text(json.dumps({"projects": []}), encoding="utf-8")

    assert load_json_object(target, label="active project snapshot") == {"projects": []}


def test_load_materialized_analysis_artifact_keeps_ready_for_present_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg = _fake_config(tmp_path)
    target = cfg.analysis_output_dir / "artifact.json"
    target.write_text(json.dumps({"value": 2}), encoding="utf-8")
    monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, cfg: SimpleNamespace(to_json=lambda: {"status": "ready", "changed": False, "reason": "ok"}),
    )

    payload, materialization = analysis_io.load_materialized_analysis_artifact("artifact.json")

    assert payload == {"value": 2}
    assert materialization["status"] == "ready"
    assert materialization["requested_artifact"] == str(target)
    assert materialization["requested_artifact_name"] == "artifact.json"
    assert materialization["requested_artifact_status"] == "ready"


def test_load_materialized_analysis_artifact_reports_missing_requested_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg = _fake_config(tmp_path)
    target = cfg.analysis_output_dir / "missing.json"
    monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, cfg: SimpleNamespace(to_json=lambda: {"status": "ready", "changed": False, "reason": "ok"}),
    )

    payload, materialization = analysis_io.load_materialized_analysis_artifact("missing.json")

    assert payload is None
    assert materialization["status"] == "missing"
    assert materialization["reason"] == f"requested analysis artifact is missing: {target}"
    assert materialization["requested_artifact_status"] == "missing"


def test_load_materialized_analysis_artifact_reports_malformed_requested_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg = _fake_config(tmp_path)
    target = cfg.analysis_output_dir / "artifact.json"
    target.write_text(json.dumps(["not", "object"]), encoding="utf-8")
    monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, cfg: SimpleNamespace(to_json=lambda: {"status": "ready", "changed": False, "reason": "ok"}),
    )

    payload, materialization = analysis_io.load_materialized_analysis_artifact("artifact.json")

    assert payload is None
    assert materialization["status"] == "malformed"
    assert materialization["reason"] == f"requested analysis artifact is malformed: {target}"
    assert materialization["requested_artifact_status"] == "malformed"


def test_load_materialized_analysis_artifact_reuses_provided_materialization(monkeypatch, tmp_path: Path) -> None:
    cfg = _fake_config(tmp_path)
    target = cfg.analysis_output_dir / "artifact.json"
    target.write_text(json.dumps({"value": 2}), encoding="utf-8")
    monkeypatch.setattr(analysis_io, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, cfg: pytest.fail("provided materialization should be reused"),
    )

    payload, materialization = analysis_io.load_materialized_analysis_artifact(
        "artifact.json",
        materialization={"status": "ready", "changed": False, "reason": "shared"},
    )

    assert payload == {"value": 2}
    assert materialization["status"] == "ready"
    assert materialization["requested_artifact_status"] == "ready"
