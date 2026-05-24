"""Runtime and deployment introspection MCP tools."""

import os
import subprocess
from pathlib import Path
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import latest_refresh_id as _latest_refresh_id


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

    return {
        "package_root": str(package_root),
        "repo_root": str(repo_root) if repo_root else None,
        "uses_repo_source": bool(repo_root and str(package_root).startswith(str(repo_root))),
        "git": {
            "head": git_head,
            "branch": git_branch,
            "dirty": bool(git_status),
        },
        "substrate": {
            "path": str(substrate_path()),
            "latest_refresh_id": latest_refresh,
            "error": substrate_error,
        },
    }
