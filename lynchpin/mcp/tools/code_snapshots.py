"""MCP tools for code snapshot status and slice discovery.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time; PEP 563 string annotations cause TypeError.
"""

from typing import Any

from lynchpin.mcp.server import app


def code_snapshot_status() -> dict[str, Any]:
    """Return the current materialization status and per-project summary for code snapshots.

    Queries code_snapshot_run in the substrate to show the current state of
    chisel-generated repomix XML slices, git bundles, and issue exports per project.
    Surfaces staleness without triggering a re-run.
    """
    from lynchpin.materialization import ensure_materialized
    from lynchpin.substrate.code_snapshots import iter_code_snapshot_runs
    from lynchpin.substrate.connection import connect
    from lynchpin.mcp.tools._utils import json_safe as _json_safe

    mat = ensure_materialized("code_snapshots", budget="manual")
    mat_payload = mat.to_json()

    projects: list[dict[str, Any]] = []
    try:
        with connect(read_only=True) as conn:
            for row in iter_code_snapshot_runs(conn):
                projects.append(_json_safe(row))
    except Exception as exc:
        mat_payload["substrate_error"] = str(exc)

    return {
        "materialization": mat_payload,
        "projects": projects,
    }


def list_code_snapshot_slices(project: str | None = None) -> dict[str, Any]:
    """List code snapshot output files from the substrate, optionally filtered by project.

    Each slice entry includes: project, filename, kind (xml_slice / xml_compressed /
    xml_issues / xml_git_log / git_bundle / working_tree_tar / repo_tree /
    combined_tar), size_bytes, and path.

    Args:
        project: Optional project name to filter results (e.g. "sinex"). If omitted,
                 returns slices for all registered projects.
    """
    from lynchpin.substrate.code_snapshots import iter_code_snapshot_slices
    from lynchpin.substrate.connection import connect
    from lynchpin.mcp.tools._utils import json_safe as _json_safe

    slices: list[dict[str, Any]] = []
    try:
        with connect(read_only=True) as conn:
            for row in iter_code_snapshot_slices(conn, project=project):
                slices.append(_json_safe(row))
    except Exception as exc:
        return {"error": str(exc), "slices": []}

    total_bytes = sum(s.get("size_bytes", 0) for s in slices)
    return {
        "project_filter": project,
        "total_slices": len(slices),
        "total_bytes": total_bytes,
        "slices": slices,
    }


@app.tool()
def code_snapshots(
    view: str = "status",
    project: str | None = None,
) -> Any:
    """Code snapshot materialization status and slice discovery. view: status (materialization state and per-project summary), slices (list output files; use project to filter)."""
    if view == "status":
        return code_snapshot_status()
    if view == "slices":
        return list_code_snapshot_slices(project=project)
    return {"error": f"unknown view {view!r}. choices: status, slices"}
