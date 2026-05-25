"""Runtime and deployment introspection MCP tools."""

import os
import subprocess
from pathlib import Path
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import latest_refresh_id as _latest_refresh_id
from lynchpin.mcp.tools._utils import registered_tool_names


def _git_value(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


@app.tool()
def mcp_runtime_status() -> dict[str, Any]:
    """Report the MCP server code path, repo revision, and latest substrate ID."""
    from lynchpin.substrate.connection import connect, substrate_path

    package_root = Path(__file__).resolve().parents[2]
    repo_root_raw = os.environ.get("LYNCHPIN_REPO_ROOT")
    repo_root = Path(repo_root_raw).resolve() if repo_root_raw else None

    git_head = _git_value(repo_root, "rev-parse", "HEAD") if repo_root else None
    git_branch = _git_value(repo_root, "branch", "--show-current") if repo_root else None
    git_status = _git_value(repo_root, "status", "--short") if repo_root else None

    try:
        with connect(substrate_path(), read_only=True) as conn:
            latest_refresh = _latest_refresh_id(conn)
    except Exception as exc:  # noqa: BLE001 - status tool should report broken substrate access.
        latest_refresh = None
        substrate_error = f"{type(exc).__name__}: {exc}"
    else:
        substrate_error = None

    live_tools = registered_tool_names()
    return {
        "package_root": str(package_root),
        "repo_root": str(repo_root) if repo_root else None,
        "uses_repo_source": bool(repo_root and str(package_root).startswith(str(repo_root))),
        "git": {
            "head": git_head,
            "branch": git_branch,
            "dirty": bool(git_status),
        },
        "mcp": {
            "registered_tool_count": len(live_tools),
        },
        "substrate": {
            "path": str(substrate_path()),
            "latest_refresh_id": latest_refresh,
            "error": substrate_error,
        },
    }


@app.tool()
def mcp_surface_self_check() -> dict[str, Any]:
    """Check contract-declared MCP tools against the live registered tool set."""
    from lynchpin.core.source_contracts import SOURCE_CONTRACTS

    status = mcp_runtime_status()
    live = set(registered_tool_names())
    declared = {
        tool
        for contract in SOURCE_CONTRACTS
        for tool in contract.mcp_tools
        if not tool.startswith("(")
    }
    missing = sorted(declared - live)
    unmapped = sorted(live - declared)
    return {
        "runtime": status,
        "declared_tool_count": len(declared),
        "registered_tool_count": len(live),
        "missing_declared_tools": missing,
        "registered_unmapped_tools": unmapped,
        "ok": not missing,
        "restart_hint": (
            "registered tools differ from source contracts; restart the MCP server after code changes"
            if missing
            else None
        ),
    }
