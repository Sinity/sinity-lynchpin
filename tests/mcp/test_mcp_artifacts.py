from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import reload_config


def _analysis_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "generated" / "analysis"
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(root))
    reload_config(monkeypatch)
    root.mkdir(parents=True)
    return root


def _stub_materialization(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_ensure_materialized(name: str, *, cfg):
        calls.append(name)
        return type(
            "Result",
            (),
            {"to_json": lambda self: {"name": name, "status": "ready", "changed": False, "reason": "ok"}},
        )()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    return calls


def test_analysis_artifact_inventory_discovers_generated_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    calls = _stub_materialization(monkeypatch)
    (root / "code_history_claims.json").write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-02T00:00:00+00:00",
                "window": {"start": "2026-05-01", "end": "2026-05-02"},
                "claims": [{"project": "sinity-lynchpin", "summary": "fixture"}],
            }
        )
    )

    from lynchpin.mcp.tools.artifacts import analysis_artifact_inventory

    result = analysis_artifact_inventory(project="sinity-lynchpin", kind="json")

    assert calls == ["analysis_artifacts"]
    assert result["summary"]["artifact_count"] == 1
    assert result["summary"]["materialization"]["status"] == "ready"
    row = result["artifacts"][0]
    assert row["name"] == "code_history_claims.json"
    assert row["status"] == "available"
    assert row["projects"] == ["sinity-lynchpin"]
    assert "code-history claims" in row["brief"]


def test_read_analysis_artifact_accepts_unique_stem_and_parses_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    calls = _stub_materialization(monkeypatch)
    (root / "workflow_mechanics.json").write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-02T00:00:00+00:00",
                "invocation_count": 2,
                "retry_chain_count": 1,
            }
        )
    )

    from lynchpin.mcp.tools.artifacts import read_analysis_artifact

    result = read_analysis_artifact("workflow_mechanics")

    assert calls == ["analysis_artifacts"]
    assert result["status"] == "available"
    assert result["name"] == "workflow_mechanics.json"
    assert result["payload"]["invocation_count"] == 2
    assert result["truncated"] is False
    assert result["materialization"]["name"] == "analysis_artifacts"
    assert result["materialization"]["status"] == "ready"


def test_read_analysis_artifact_reports_missing_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    calls = _stub_materialization(monkeypatch)

    from lynchpin.mcp.tools.artifacts import read_analysis_artifact

    result = read_analysis_artifact("../nope")

    assert calls == ["analysis_artifacts"]
    assert result["status"] == "missing"
    assert "No generated analysis artifact" in result["reason"]
    assert result["materialization"]["name"] == "analysis_artifacts"
    assert result["materialization"]["status"] == "missing"
    assert result["materialization"]["requested_artifact_name"] == "../nope"


def test_read_analysis_artifact_bounds_oversized_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    calls = _stub_materialization(monkeypatch)
    (root / "huge.md").write_text("abcdef")

    from lynchpin.mcp.tools.artifacts import read_analysis_artifact

    result = read_analysis_artifact("huge", max_bytes=3)

    assert calls == ["analysis_artifacts"]
    assert result["status"] == "available"
    assert result["truncated"] is True
    assert result["text"] == "abc"
    assert "bounded text excerpt" in result["reason"]
    assert result["materialization"]["name"] == "analysis_artifacts"
    assert result["materialization"]["status"] == "ready"
