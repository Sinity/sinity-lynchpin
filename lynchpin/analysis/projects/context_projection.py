"""Owner-selected project orientation over one retained observation."""

from __future__ import annotations

import json
from typing import Any


def _bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _fields(row: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row[name] for name in names if name in row}


def compact_context(result: dict[str, Any], budget: int) -> dict[str, Any]:
    """Select domain fields, then admit whole rows fairly within an exact byte bound.

    References are JSON pointers into the complete owner product. They identify
    this observation, unlike replaying the owner request against changing runtime.
    Observed counts describe the retained inputs, never a complete live census.
    """
    output = {
        "schema_version": 1,
        "product": "project_context_compact",
        "project": result["project"],
        "intent": result["intent"],
        "temporal": result["temporal"],
        "outcome": result["outcome"],
        "components": [],
        "gaps": result["gaps"],
        "projection": "Selected fields and bounded rows; full data is in the retained observation",
    }
    candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for index, component in enumerate(result["components"]):
        data = component["data"]
        name = component["name"]
        entry = _fields(component, (
            "name", "owner", "status", "source_ref", "source_revision",
            "refresh_id", "coverage", "payload_revision",
        ))
        entry["full_data_pointer"] = f"/components/{index}/data"
        entry["freshness"] = _fields(data, ("observed_at", "watermark", "temporal"))
        gaps = data.get("gaps", [])
        entry["gaps"] = [gap[:160] if isinstance(gap, str) else gap for gap in gaps[:1]]
        entry["gaps_truncated"] = any(isinstance(gap, str) and len(gap) > 160 for gap in gaps[:1])
        entry["gaps_omitted"] = max(0, len(gaps) - 1)
        entry["sections"] = {}
        output["components"].append(entry)

        def section(key: str, rows: list[dict[str, Any]]) -> None:
            target = {"items": [], "observed_count": len(rows), "omitted_count": len(rows)}
            entry["sections"][key] = target
            candidates.append((target, rows))

        if name == "tasks":
            rows = sorted(data.get("nodes", []), key=lambda row: (
                {"in_progress": 0, "open": 1, "blocked": 2}.get(row.get("status"), 3),
                row.get("id", ""),
            ))
            selected = []
            for row in rows:
                item = _fields(row, ("id", "ref", "status", "priority", "title", "assignee", "role", "bead_revision"))
                if isinstance(item.get("title"), str) and len(item["title"]) > 240:
                    item["title"] = item["title"][:240]
                    item["title_truncated"] = True
                readiness = data.get("readiness")
                item["readiness"] = row.get("readiness", readiness.get(row.get("id")) if isinstance(readiness, dict) else None)
                selected.append(item)
            section("work", selected)
        elif name == "runtime":
            entry["binding_authority"] = "Retained AgentCTL records; bindings do not prove a live process"
            bindings = []
            for run in data.get("rows", []):
                for worker in run.get("workers", []):
                    bindings.append({
                        "run_id": run["run_id"],
                        **_fields(worker, ("id", "beads", "stage", "status", "task_id", "task_reference", "job_id", "job_launch_reference", "worktree")),
                    })
            section("bindings", bindings)
        elif name == "evidence":
            for key in ("claims", "salient_chains", "salient_anomalies", "caveats"):
                section(key, data.get(key, []))
        elif name == "trajectory":
            entry["counts"] = data.get("counts", {})
            section("days", [_fields(row, ("day", "attempts_observed", "published_batches_observed")) for row in reversed(data.get("rows", []))])
        elif name == "verification":
            entry["counts"] = data.get("counts", {})
            section("changes", [_fields(row, ("bead_ref", "ac_ids", "command", "transitions")) for row in data.get("groups", [])])

    # Oversized mandatory provenance is an explicit unusable projection. Never
    # cut a revision/ref into an address that names a different observation.
    if _bytes(output) > budget:
        return {"product": "project_context_compact", "outcome": "unavailable", "reason": "Required provenance exceeds the presentation budget", "components": []}
    for offset in range(max((len(rows) for _, rows in candidates), default=0)):
        for target, rows in candidates:
            if offset >= len(rows):
                continue
            target["items"].append(rows[offset])
            target["omitted_count"] -= 1
            if _bytes(output) > budget:
                target["items"].pop()
                target["omitted_count"] += 1
    return output
