"""Bounded project context with independently reported owner coverage."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any

from lynchpin.analysis.projects.campaign_history import (
    project_trajectory,
    verification_regression,
)
from lynchpin.analysis.projects.owner_products import classify_task_scope
from lynchpin.core.serialization import jsonable
from lynchpin.core.projects import canonical_project_name
from lynchpin.graph.context_pack import project_graph_context
from lynchpin.sources.beads import read_tasks
from lynchpin.sources.campaign import read_batches, revision


def _graph(project: str, start: date, end: date, refresh_id: str | None) -> Any:
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.graph import load_evidence_graph

    with connect(read_only=True) as conn:
        graph = load_evidence_graph(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
            projects=(canonical_project_name(project) or project,),
        )
    if graph is None or not isinstance(graph.refresh_id, str) or not graph.refresh_id:
        raise ValueError(
            "No retained graph generation covers the requested project window"
        )
    if refresh_id is not None and graph.refresh_id != refresh_id:
        raise ValueError("Graph reader returned a different refresh generation")
    if graph.start > start or graph.end < end:
        raise ValueError("Requested graph generation does not cover the context window")
    return graph


def _component(
    name: str,
    owner: str,
    source_ref: str,
    data: dict[str, Any],
    *,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    data = jsonable(data)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    coverage = data.get("coverage", "retained_graph" if refresh_id else "unknown")
    unavailable = coverage == "unavailable"
    return {
        "name": name,
        "owner": owner,
        "status": "unavailable" if unavailable else "available",
        "source_ref": source_ref,
        "source_revision": data.get("revision", data.get("task_revision", refresh_id)),
        "refresh_id": refresh_id,
        "coverage": coverage,
        "data": data,
        "gaps": data.get("gaps", []),
        "payload_bytes": len(payload),
        "payload_revision": revision(data),
    }


def project_context(
    *,
    project: str,
    intent: str = "project.orientation",
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
    roots: list[str] | None = None,
    at: str | None = None,
    budget_bytes: int = 56000,
) -> dict[str, Any]:
    if not 8192 <= budget_bytes <= 262144:
        raise ValueError("budget_bytes must be between 8192 and 262144")
    end_date = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=13)
    if start_date > end_date or (end_date - start_date).days > 366:
        raise ValueError("Context window must be ordered and at most 367 days")
    components = []
    selected_refresh = None
    graph_ref = f"lynchpin://projects/{project}/graph/{refresh_id or 'latest'}"
    try:
        graph = _graph(project, start_date, end_date, refresh_id)
        selected_refresh = graph.refresh_id
        graph_ref = f"lynchpin://projects/{project}/graph/{selected_refresh}"
        components.append(
            _component(
                "evidence",
                "lynchpin",
                graph_ref,
                project_graph_context(
                    graph,
                    project=canonical_project_name(project) or project,
                    start=start_date,
                    end=end_date,
                ),
                refresh_id=selected_refresh,
            )
        )
    except Exception as error:
        # A component failure cannot erase evidence from another owner.
        components.append(
            _component(
                "evidence",
                "lynchpin",
                graph_ref,
                {"coverage": "unavailable", "gaps": [str(error)]},
            )
        )
    prefix = f"sinnix://projects/{project}/beads/"
    requested_refs = (
        [root if root.startswith(prefix) else prefix + root for root in roots]
        if roots
        else None
    )
    task_ref = f"beads://projects/{project}/owner/read"
    try:
        tasks = classify_task_scope(read_tasks(project, roots=roots, at=at))
        refs = (
            ([row["ref"] for row in tasks["nodes"]] or requested_refs)
            if roots
            else None
        )
        components.append(_component("tasks", "beads", tasks["source_ref"], tasks))
    except Exception as error:
        tasks = {
            "nodes": [],
            "coverage": "unavailable",
            "gaps": [str(error)],
            "source_ref": task_ref,
        }
        refs = requested_refs
        components.append(_component("tasks", "beads", task_ref, tasks))
    try:
        runtime = read_batches(project)
        if (
            not isinstance(runtime, dict)
            or not isinstance(runtime.get("rows"), list)
            or any(not isinstance(row, dict) for row in runtime["rows"])
            or (
                runtime.get("revision") is not None
                and not isinstance(runtime["revision"], str)
            )
        ):
            raise ValueError("AgentCTL returned an invalid runtime snapshot")
    except Exception as error:
        runtime = {"rows": [], "coverage": "unavailable", "gaps": [str(error)]}
    for name, function in (
        ("trajectory", project_trajectory),
        ("verification", verification_regression),
    ):
        source_ref = f"agentctl://projects/{project}/batches"
        try:
            product = function(
                project=project, bead_refs=refs, runtime_snapshot=runtime
            )
            product["revision"] = runtime.get("revision")
            if runtime.get("coverage") == "unavailable":
                product["coverage"] = "unavailable"
            components.append(_component(name, "agentctl", source_ref, product))
        except Exception as error:
            components.append(
                _component(
                    name,
                    "agentctl",
                    source_ref,
                    {
                        "coverage": "unavailable",
                        "revision": runtime.get("revision"),
                        "gaps": [str(error)],
                    },
                )
            )
    request = {
        "action": "project_context",
        "project": project,
        "intent": intent,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "refresh_id": selected_refresh or refresh_id,
        "roots": roots,
        "at": tasks.get("revision") or at,
        "budget_bytes": budget_bytes,
    }
    result = {
        "schema_version": 1,
        "product": "project_context",
        "project": project,
        "intent": intent,
        "temporal": {
            "refresh_id": selected_refresh,
            "requested_refresh_id": refresh_id,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "task_revision": tasks.get("revision"),
        },
        "components": components,
        "total_budget_bytes": budget_bytes,
        "owner_ref": {"tool": "lynchpin_project", "arguments": request},
        "gaps": [
            "Task and runtime revisions are independent owner observations; only graph evidence belongs to the selected refresh generation"
        ],
    }
    # The gateway persists the exact owner observation before budgeting its presentation.
    for component in components:
        component["presentation_budget_exceeded"] = (
            component["payload_bytes"] > budget_bytes
        )
    result["outcome"] = (
        "partial" if any(c["status"] == "unavailable" for c in components) else "ok"
    )
    result["presentation_budget_exceeded"] = (
        len(json.dumps(result, ensure_ascii=False).encode()) > budget_bytes
    )
    return result
