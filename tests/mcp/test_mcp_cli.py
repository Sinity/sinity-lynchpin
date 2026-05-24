from __future__ import annotations

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
