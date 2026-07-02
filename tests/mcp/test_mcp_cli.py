from __future__ import annotations

import json
import subprocess
import sys


def test_mcp_help_exits_without_starting_stdio_server() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lynchpin.mcp", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert "Run the Lynchpin MCP server over stdio" in result.stdout


def test_mcp_version_exits_without_starting_stdio_server() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lynchpin.mcp", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("lynchpin-mcp ")


def test_mcp_catalog_guide_exits_without_starting_stdio_server() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lynchpin.mcp", "--guide"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["kind"] == "lynchpin_mcp_catalog"
    assert payload["data"]["tool_count"] == 8
    assert payload["meta"]["tool"] == "lynchpin_catalog"
    assert payload["meta"]["action"] == "catalog"
    assert "old_route_map" not in payload["data"]


def test_mcp_self_check_exits_without_starting_stdio_server() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lynchpin.mcp", "--self-check"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["ok"] is True
    assert payload["data"]["registered_tool_count"] == 8
    assert payload["data"]["unexpected_tools"] == []
