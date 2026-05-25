from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import setup_substrate


def test_mcp_runtime_status_reports_source_and_substrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.runtime import mcp_runtime_status

    status = mcp_runtime_status()

    assert status["repo_root"]
    assert "package_root" in status
    assert status["substrate"]["path"].endswith("substrate.duckdb")
    assert "latest_refresh_id" in status["substrate"]
    assert status["mcp"]["registered_tool_count"] > 0


def test_mcp_surface_self_check_reports_contract_tool_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.runtime import mcp_surface_self_check

    status = mcp_surface_self_check()

    assert status["declared_tool_count"] > 0
    assert status["registered_tool_count"] >= status["declared_tool_count"]
    assert status["missing_declared_tools"] == []
    assert status["ok"] is True


def test_mcp_capability_matrix_reports_contract_capabilities_without_stale_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.capability import mcp_capability_matrix

    rows = {row["source"]: row for row in mcp_capability_matrix()}

    takeout = rows["google_takeout"]
    assert "google_takeout_events" in takeout["mcp_tools"]
    assert "google_activity_day" in takeout["graph_node_kinds"]
    assert "freshness" not in takeout
    assert "last_date" in takeout["date_bounds"]

    terminal = rows["atuin"]
    assert terminal["collection_model"] == "continuous"
    assert "terminal_daily" in terminal["mcp_tools"]
