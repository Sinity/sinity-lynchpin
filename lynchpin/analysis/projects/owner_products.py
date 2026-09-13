"""Project products that acquire exact task state from its owner."""

from __future__ import annotations

from typing import Any

from lynchpin.analysis.projects.campaign import (
    _role,
    campaign_evidence,
    campaign_scope_delta,
)
from lynchpin.analysis.projects.campaign_history import (
    project_trajectory,
    verification_regression,
)
from lynchpin.sources.beads import read_tasks


def classify_task_scope(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Interpret declared analytical roles without altering native readiness."""
    nodes = [dict(row, role=_role(row)) for row in snapshot["nodes"]]
    return {
        **snapshot,
        "nodes": nodes,
        "declared_gates": sorted(row["id"] for row in nodes if row["role"] == "gate"),
        "declared_decisions": sorted(
            row["id"] for row in nodes if row["role"] == "decision"
        ),
        "unknown_roles": sorted(row["id"] for row in nodes if row["role"] == "unknown"),
    }


def campaign_product(
    *,
    action: str,
    project: str,
    roots: list[str],
    at: str | None = None,
    baseline: str | None = None,
    relation: str = "blocks",
    direction: str = "prerequisites",
    max_nodes: int = 500,
    max_depth: int = 50,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    if not roots:
        raise ValueError("Explicit campaign roots are required")
    if action == "campaign_scope_delta" and not baseline:
        raise ValueError("baseline is required for campaign_scope_delta")
    options = dict(
        roots=roots,
        relation=relation,
        direction=direction,
        max_nodes=max_nodes,
        max_depth=max_depth,
    )
    snapshot = classify_task_scope(read_tasks(project, at=at, **options))
    before = (
        classify_task_scope(read_tasks(project, at=baseline, **options))
        if baseline
        else None
    )
    delta = campaign_scope_delta(before, snapshot) if before is not None else None
    if action == "campaign_scope_delta":
        assert delta is not None
        return {**delta, "closure": snapshot, "baseline": before}
    references = [row["ref"] for row in snapshot["nodes"]]
    if not references:
        prefix = f"sinnix://projects/{project}/beads/"
        references = [
            root if root.startswith(prefix) else prefix + root for root in roots
        ]
    evidence = campaign_evidence(
        project=project,
        bead_refs=references,
        task_snapshot=snapshot,
        refresh_id=refresh_id,
    )
    return {
        **evidence,
        "product": action,
        "closure": snapshot,
        "baseline": before,
        "scope_delta": delta,
    }


def history_product(
    *,
    action: str,
    project: str,
    roots: list[str] | None = None,
    at: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    snapshot = classify_task_scope(read_tasks(project, roots=roots, at=at))
    function = (
        verification_regression
        if action == "verification_regression"
        else project_trajectory
    )
    # Absent task coverage must not hide independently retained runtime records.
    refs = (
        [row["ref"] for row in snapshot["nodes"]]
        if roots and snapshot["nodes"]
        else roots or None
    )
    if refs:
        prefix = f"sinnix://projects/{project}/beads/"
        refs = [ref if ref.startswith(prefix) else prefix + ref for ref in refs]
    result = function(project=project, bead_refs=refs, refresh_id=refresh_id)
    result["tasks"] = snapshot
    result["sources"]["beads"] = {
        key: snapshot.get(key)
        for key in ("owner", "revision", "coverage", "gaps", "source_ref")
    }
    result["gaps"].extend(snapshot.get("gaps", []))
    return result
