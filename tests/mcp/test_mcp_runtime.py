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
