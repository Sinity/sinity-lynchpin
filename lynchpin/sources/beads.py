"""Revision-pinned task snapshots from the Beads owner read contract."""

from __future__ import annotations

from collections.abc import Callable
import json
import re
import subprocess
from typing import Any

from lynchpin.core.projects import canonical_project_name, project_path


def _read(project: str, request: dict[str, Any]) -> Any:
    canonical = canonical_project_name(project)
    if canonical is None:
        raise ValueError(f"Unknown registered project: {project}")
    result = subprocess.run(
        ["bd", "owner", "read", "--json"],
        input=json.dumps(request),
        cwd=project_path(canonical),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def read_tasks(
    project: str,
    *,
    roots: list[str] | None = None,
    at: str | None = None,
    relation: str = "blocks",
    direction: str = "prerequisites",
    max_nodes: int = 500,
    max_depth: int = 50,
    loader: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Read one bounded owner snapshot; never manufacture a complete denominator."""
    prefix = f"sinnix://projects/{project}/beads/"
    ids = [root.removeprefix(prefix) for root in roots or []]
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", root) for root in ids):
        raise ValueError(
            "roots must be task IDs or canonical references in the selected project"
        )
    if relation not in {"blocks", "parent-child"} or direction not in {
        "prerequisites",
        "dependents",
    }:
        raise ValueError("Unsupported campaign relation or direction")
    if not 1 <= max_nodes <= 1000 or not 1 <= max_depth <= 100:
        raise ValueError("Campaign bounds exceed the owner contract")
    request: dict[str, Any] = {
        "at": at or "HEAD",
        "limit": max_nodes,
        "include_closed": True,
        "provenance": True,
        **(
            {
                "roots": ids,
                "relations": [relation],
                "direction": "dependencies"
                if direction == "prerequisites"
                else direction,
                "depth": max_depth,
            }
            if ids
            else {}
        ),
    }
    source_ref = f"beads://projects/{project}/owner/read"
    try:
        payload = (loader or _read)(project, request)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("revision"), str)
            or not payload["revision"]
            or not isinstance(payload.get("items"), list)
            or not isinstance(payload.get("temporal"), dict)
        ):
            raise ValueError("Beads returned an invalid revision-pinned snapshot")
        revision = payload["revision"]
        if payload["temporal"].get("resolved_revision") != revision:
            raise ValueError("Beads snapshot mixes task generations")
        closure = payload.get("closure")
        if not isinstance(closure, dict) or not isinstance(
            closure.get("complete"), bool
        ):
            raise ValueError("Beads returned an invalid closure object")
        if not isinstance(payload.get("has_more"), bool):
            raise ValueError("Beads returned invalid pagination coverage")
        for name in ("edges", "provenance_edges"):
            edges = payload.get(name, [])
            if not isinstance(edges, list) or any(
                not isinstance(edge, dict) for edge in edges
            ):
                raise ValueError(f"Beads returned invalid {name}")
        provenance = payload.get(
            "provenance_coverage", {"complete": False, "state": "unavailable"}
        )
        if not isinstance(provenance, dict) or not isinstance(
            provenance.get("complete"), bool
        ):
            raise ValueError("Beads returned invalid provenance coverage")
        for name, values in (
            ("graph_leaves", payload.get("graph_leaves")),
            ("closure.graph_leaves", closure.get("graph_leaves")),
            ("closure.missing_ids", closure.get("missing_ids")),
        ):
            if values is not None and (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
            ):
                raise ValueError(f"Beads returned invalid {name}")
        nodes = []
        for row in payload["items"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("Beads returned an invalid task row")
            nodes.append(
                {
                    **row,
                    "ref": prefix + row["id"],
                    "task_revision": revision,
                    "bead_revision": row.get("bead_revision", row.get("revision")),
                    "bead_revision_domain": "beads_row_revision",
                    "bead_revision_coverage": "partial_update_coverage"
                    if row.get("bead_revision", row.get("revision")) is not None
                    else "unavailable",
                }
            )
        complete = closure.get("complete") is True and payload.get("has_more") is False
        gaps = (
            []
            if complete
            else ["Bounded Beads selection does not establish complete task scope"]
        )
        return {
            **{
                key: payload[key]
                for key in ("readiness", "cycles", "frontier", "counts")
                if key in payload
            },
            "owner": "beads",
            "interface": "bd.owner.read",
            "project_id": project,
            "task_revision": revision,
            "revision": revision,
            "temporal": payload["temporal"],
            "nodes": nodes,
            "edges": payload.get("edges", []),
            "provenance_edges": payload.get("provenance_edges", []),
            "provenance_coverage": payload.get(
                "provenance_coverage", {"complete": False, "state": "unavailable"}
            ),
            "graph_leaves": payload.get("graph_leaves"),
            "closure": closure,
            "complete": complete,
            "coverage": {
                "state": "complete" if complete else "partial",
                "complete": complete,
            },
            "gaps": gaps,
            "source_ref": source_ref,
            "owner_request": {**request, "at": revision},
        }
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {
            "owner": "beads",
            "interface": "bd.owner.read",
            "project_id": project,
            "nodes": [],
            "complete": False,
            "coverage": "unavailable",
            "gaps": [str(error)],
            "source_ref": source_ref,
            "temporal": {"requested": at or "HEAD", "resolved_revision": None},
        }
