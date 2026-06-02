"""MCP tools for generated analysis artifacts.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

import json
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import dataclass_to_json_dict
from lynchpin.mcp.tools._utils import json_safe as _json_safe

_MAX_ARTIFACT_BYTES = 2_000_000


def _artifact_rows(
    *,
    project: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    from lynchpin.sources.analysis_artifacts import artifact_inventory

    selected_project = project.lower() if project else None
    selected_kind = kind.lower() if kind else None
    selected_status = status.lower() if status else None
    rows: list[dict[str, Any]] = []
    for artifact in artifact_inventory():
        if selected_project is not None and selected_project not in {
            p.lower() for p in artifact.projects
        }:
            continue
        if selected_kind is not None and artifact.kind.lower() != selected_kind:
            continue
        if selected_status is not None and artifact.status.lower() != selected_status:
            continue
        row = dataclass_to_json_dict(artifact)
        row["path"] = str(artifact.path)
        rows.append(row)
    return rows


def _resolve_artifact(name: str) -> dict[str, Any] | None:
    rows = _artifact_rows()
    by_name = {row["name"]: row for row in rows}
    if name in by_name:
        return by_name[name]

    normalized = name.removesuffix(".json").removesuffix(".md").removesuffix(".html")
    matches = [
        row for row in rows
        if str(row["name"]).rsplit("/", 1)[-1].rsplit(".", 1)[0] == normalized
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return {
            "status": "ambiguous",
            "name": name,
            "matches": [row["name"] for row in matches],
        }
    return None


@app.tool()
def analysis_artifact_inventory(
    project: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    """List generated analysis artifacts visible to MCP consumers.

    This is the discovery surface for durable JSON/Markdown/HTML products under
    the configured analysis output directory. It does not run analyses, mutate
    state, or read raw external data.
    """
    effective_limit = min(max(limit, 1), 1000)
    rows = _artifact_rows(project=project, kind=kind, status=status)
    return {
        "summary": {
            "artifact_count": len(rows),
            "returned_count": min(len(rows), effective_limit),
            "truncated": len(rows) > effective_limit,
            "project": project,
            "kind": kind,
            "status": status,
        },
        "artifacts": rows[:effective_limit],
    }


@app.tool()
def read_analysis_artifact(
    name: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Read one generated analysis artifact by relative name or unique stem.

    The read is bounded. JSON artifacts at or below ``max_bytes`` are parsed and
    returned as ``payload``; oversized artifacts return metadata and an excerpt
    so agents can discover the artifact without accidentally pulling megabytes
    through MCP.
    """
    row = _resolve_artifact(name)
    if row is None:
        return {
            "status": "missing",
            "name": name,
            "reason": "No generated analysis artifact matched that name or unique stem.",
        }
    if row.get("status") == "ambiguous":
        return row

    path_text = row.get("path")
    if not isinstance(path_text, str):
        return {
            "status": "partial",
            "name": name,
            "reason": "Resolved artifact row has no path.",
        }

    from pathlib import Path

    path = Path(path_text)
    cap = min(max(max_bytes, 1), _MAX_ARTIFACT_BYTES)
    try:
        size = path.stat().st_size
        if size > cap:
            with path.open("rb") as handle:
                raw = handle.read(cap)
            text = raw.decode("utf-8", errors="replace")
        else:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
    except OSError as exc:
        return {
            "status": "partial",
            "name": row["name"],
            "metadata": row,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    truncated = size > cap
    result: dict[str, Any] = {
        "status": "available",
        "name": row["name"],
        "metadata": row,
        "bytes": size,
        "returned_bytes": len(raw),
        "truncated": truncated,
    }
    if not truncated and row.get("kind") == "json":
        try:
            result["payload"] = _json_safe(json.loads(text))
        except json.JSONDecodeError as exc:
            result["status"] = "partial"
            result["reason"] = f"JSONDecodeError: {exc}"
            result["text"] = text
        return result

    result["text"] = text
    if truncated:
        result["reason"] = "Artifact exceeded max_bytes; returned bounded text excerpt only."
    return result


__all__ = ["analysis_artifact_inventory", "read_analysis_artifact"]
