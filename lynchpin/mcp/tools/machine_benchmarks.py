"""Machine benchmark, validation, matched experiment, and attribution-candidate tools."""
from typing import Any

from lynchpin.mcp.tools._machine_helpers import _analysis_artifact
from lynchpin.mcp.tools._utils import json_safe as _json_safe


def machine_experiment_claims(
    claim_mode: str | None = None,
    workload: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read manifest-backed machine experiment claim packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "claim_packs": []}
    packs = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    rows = [
        row
        for row in packs
        if (claim_mode is None or row.get("claim_mode") == claim_mode)
        and (workload is None or row.get("workload") == workload)
    ]
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("run_id") or "")))
    summary = {
        "run_count": payload.get("run_count"),
        "controlled_claim_count": payload.get("controlled_claim_count"),
        "observational_claim_count": payload.get("observational_claim_count"),
        "by_manifest_validation_status": _count_manifest_validation_status(rows),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "claim_packs": rows[:max(limit, 0)]}


def machine_benchmark_runs(
    limit: int = 100,
    run_group_id: str | None = None,
    workload: str | None = None,
) -> dict[str, Any]:
    """Read manifest-backed benchmark/experiment run claim packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "runs": []}
    rows = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    if run_group_id:
        rows = [row for row in rows if row.get("run_group_id") == run_group_id]
    if workload:
        rows = [row for row in rows if row.get("workload") == workload]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "run_count": payload.get("run_count", len(rows)),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "observational_claim_count": payload.get("observational_claim_count"),
            "by_manifest_validation_status": _count_manifest_validation_status(rows),
            "caveats": payload.get("caveats", []),
        },
        "runs": rows[:max(limit, 0)],
    }


def machine_benchmark_phases(
    limit: int = 200,
    run_id: str | None = None,
    derivation: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    """Read parsed Nix internal-json phases embedded in benchmark run packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "phases": []}
    rows = []
    for pack in payload.get("claim_packs", []):
        if not isinstance(pack, dict):
            continue
        if run_id and pack.get("run_id") != run_id:
            continue
        _internal = pack.get("internal_json")
        internal_json: dict[str, Any] = _internal if isinstance(_internal, dict) else {}
        for row in internal_json.get("phases", []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("activity_type") or "")
            if phase and phase not in name:
                continue
            if derivation and derivation not in name:
                continue
            rows.append({"run_id": pack.get("run_id"), "run_group_id": pack.get("run_group_id"), **row})
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "phase_count": len(rows),
            "caveats": payload.get("caveats", []),
        },
        "phases": rows[:max(limit, 0)],
    }


def machine_benchmark_estimates(run_group_id: str | None = None, metric: str | None = None) -> dict[str, Any]:
    """Read effect estimates, intervals, and randomization p-values for benchmark run groups."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "estimates": []}
    rows = [row for row in payload.get("effect_estimates", []) if isinstance(row, dict)]
    if run_group_id:
        rows = [row for row in rows if row.get("run_group_id") == run_group_id]
    if metric:
        rows = [row for row in rows if row.get("metric") == metric]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "estimate_count": len(rows),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "caveats": payload.get("caveats", []),
        },
        "estimates": rows,
    }


def machine_discovery_validation_splits(
    candidate_id: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read discovery/validation split metadata for machine mining designs."""
    del candidate_id
    payload = _analysis_artifact("machine_validation_design.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "split": None, "boundaries": []}
    boundaries = [row for row in payload.get("boundaries", []) if isinstance(row, dict)]
    if project:
        boundaries = [
            row for row in boundaries
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "boundary_count": payload.get("boundary_count", len(boundaries)),
            "caveats": payload.get("caveats", []),
        },
        "split": payload.get("split"),
        "boundaries": boundaries,
    }


def machine_boundary_candidates(
    limit: int = 100,
    boundary_type: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read candidate natural-experiment boundaries from validation design."""
    payload = _analysis_artifact("machine_validation_design.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "boundaries": []}
    boundaries = [row for row in payload.get("boundaries", []) if isinstance(row, dict)]
    if boundary_type:
        boundaries = [row for row in boundaries if row.get("boundary_type") == boundary_type]
    if project:
        boundaries = [
            row for row in boundaries
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "boundary_count": payload.get("boundary_count", len(boundaries)),
            "caveats": payload.get("caveats", []),
        },
        "boundaries": boundaries[:max(limit, 0)],
    }


def machine_matched_designs(limit: int = 100, status: str | None = None) -> dict[str, Any]:
    """Read matched boundary designs, placebo probes, and balance diagnostics."""
    payload = _analysis_artifact("machine_matched_designs.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "designs": []}
    designs = [row for row in payload.get("designs", []) if isinstance(row, dict)]
    if status:
        designs = [row for row in designs if row.get("identification_status") == status]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "design_count": payload.get("design_count", len(designs)),
            "supportable_design_count": payload.get("supportable_design_count"),
            "caveats": payload.get("caveats", []),
        },
        "designs": designs[:max(limit, 0)],
    }


def machine_matched_comparisons(
    limit: int = 100,
    candidate_id: str | None = None,
    boundary_id: str | None = None,
) -> dict[str, Any]:
    """Read matched comparison designs, optionally restricted by candidate or boundary."""
    payload = _analysis_artifact("machine_matched_designs.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "comparisons": []}
    designs = [row for row in payload.get("designs", []) if isinstance(row, dict)]
    if candidate_id:
        designs = [row for row in designs if row.get("candidate_id") == candidate_id]
    if boundary_id:
        designs = [row for row in designs if row.get("boundary_id") == boundary_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "comparison_count": payload.get("design_count", len(designs)),
            "supportable_design_count": payload.get("supportable_design_count"),
            "caveats": payload.get("caveats", []),
        },
        "comparisons": designs[:max(limit, 0)],
    }


def machine_negative_controls(limit: int = 100, status: str | None = None, boundary_id: str | None = None) -> dict[str, Any]:
    """Read negative-control and placebo checks over matched boundary designs."""
    payload = _analysis_artifact("machine_negative_controls.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "controls": []}
    rows = [row for row in payload.get("controls", []) if isinstance(row, dict)]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if boundary_id:
        rows = [row for row in rows if row.get("boundary_id") == boundary_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "control_count": payload.get("control_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "controls": rows[:max(limit, 0)],
    }


def machine_comparisons(limit: int = 100, signal: str | None = None) -> dict[str, Any]:
    """Read observational cohort-vs-rest machine contrast estimates."""
    payload = _analysis_artifact("machine_comparisons.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "contrasts": []}
    contrasts = [row for row in payload.get("contrasts", []) if isinstance(row, dict)]
    if signal:
        contrasts = [row for row in contrasts if row.get("statistical_signal") == signal]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "contrast_count": payload.get("contrast_count", len(contrasts)),
            "multiplicity_policy": payload.get("multiplicity_policy"),
            "caveats": payload.get("caveats", []),
        },
        "contrasts": contrasts[:max(limit, 0)],
    }


def machine_attribution_candidates(
    limit: int = 25,
    validation_status: str | None = None,
    mechanism_family: str | None = None,
    pareto_frontier: bool | None = None,
) -> dict[str, Any]:
    """Read non-causal machine attribution candidates from the analysis artifact."""
    payload = _analysis_artifact("machine_attribution_candidates.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "candidates": []}
    candidates = [row for row in payload.get("candidates", []) if isinstance(row, dict)]
    if validation_status:
        candidates = [row for row in candidates if row.get("validation_status") == validation_status]
    if mechanism_family:
        candidates = [row for row in candidates if row.get("mechanism_family") == mechanism_family]
    if pareto_frontier is not None:
        candidates = [row for row in candidates if bool(row.get("pareto_frontier")) is pareto_frontier]
    candidates.sort(key=lambda row: -float(row.get("priority_score") or 0.0))
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "candidate_count": payload.get("candidate_count", len(candidates)),
            "pareto_frontier_count": payload.get("pareto_frontier_count"),
            "pareto_frontier_ids": payload.get("pareto_frontier_ids", []),
            "by_validation_status": _count_by(candidates, "validation_status"),
            "by_mechanism_family": _count_by(candidates, "mechanism_family"),
            "caveats": payload.get("caveats", []),
        },
        "candidates": candidates[:max(limit, 0)],
    }


def machine_benchmark_plans(
    limit: int = 25,
    status: str | None = None,
    run_group_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Read dry-run controlled benchmark plans generated from candidates."""
    payload = _analysis_artifact("machine_benchmark_plans.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "plans": []}
    plans = [row for row in payload.get("plans", []) if isinstance(row, dict)]
    if status:
        plans = [row for row in plans if row.get("planning_status") == status]
    if run_group_id:
        plans = [
            row for row in plans
            if isinstance(row.get("manifest_preview"), dict)
            and (row["manifest_preview"].get("controlled_benchmark") or {}).get("run_group_id") == run_group_id
        ]
    if candidate_id:
        plans = [row for row in plans if row.get("candidate_id") == candidate_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "plan_count": payload.get("plan_count", len(plans)),
            "ready_plan_count": payload.get("ready_plan_count"),
            "caveats": payload.get("caveats", []),
        },
        "plans": plans[:max(limit, 0)],
    }


def machine_benchmark_plan_template(candidate_id: str) -> dict[str, Any]:
    """Return the benchmark manifest preview for a single candidate."""
    payload = _analysis_artifact("machine_benchmark_plans.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "plan": None, "manifest_preview": None}
    for row in payload.get("plans", []):
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id:
            return {
                "summary": {
                    "generated_at_utc": payload.get("generated_at_utc"),
                    "generated_for": payload.get("generated_for"),
                    "candidate_id": candidate_id,
                    "planning_status": row.get("planning_status"),
                    "readiness": row.get("readiness"),
                },
                "plan": row,
                "manifest_preview": row.get("manifest_preview"),
                "run_manifest": row.get("run_manifest", []),
            }
    return {"summary": {"status": "not_found", "candidate_id": candidate_id}, "plan": None, "manifest_preview": None}


def machine_benchmark_manifest_bundle(limit: int = 10) -> dict[str, Any]:
    """Read exportable benchmark manifest templates for ready plans."""
    payload = _analysis_artifact("machine_benchmark_manifest_bundle.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "groups": []}
    groups = [row for row in payload.get("groups", []) if isinstance(row, dict)]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "group_count": payload.get("group_count", len(groups)),
            "run_template_count": payload.get("run_template_count"),
            "caveats": payload.get("caveats", []),
        },
        "groups": groups[:max(limit, 0)],
    }


def machine_benchmark_execution_handoff(limit: int = 10, ready_only: bool = False) -> dict[str, Any]:
    """Read ranked benchmark groups ready for future export/execution."""
    payload = _analysis_artifact("machine_benchmark_execution_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "items": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    if ready_only:
        rows = [row for row in rows if row.get("ready_to_export") is True]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "handoff_count": payload.get("handoff_count", len(rows)),
            "ready_group_count": payload.get("ready_group_count"),
            "blocked_group_count": payload.get("blocked_group_count"),
            "run_template_count": payload.get("run_template_count"),
            "ready_run_count": payload.get("ready_run_count"),
            "caveats": payload.get("caveats", []),
        },
        "items": rows[:max(limit, 0)],
    }


def machine_benchmark_selected_runbook(
    run_group_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Return the operational command sequence for executing one ready benchmark group."""
    payload = _analysis_artifact("machine_benchmark_execution_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "commands": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    rows = [
        row for row in rows
        if row.get("ready_to_export") is True
        and (run_group_id is None or row.get("run_group_id") == run_group_id)
        and (candidate_id is None or row.get("candidate_id") == candidate_id)
    ]
    rows.sort(key=lambda row: (not bool(row.get("pareto_frontier")), -float(row.get("priority_score") or 0), str(row.get("run_group_id") or "")))
    if not rows:
        return {
            "summary": {
                "status": "not_found",
                "run_group_id": run_group_id,
                "candidate_id": candidate_id,
            },
            "commands": [],
        }
    row = rows[0]
    command = [
        "python",
        "-m",
        "lynchpin.analysis",
        "machine-benchmark-run-selected",
        "--run-group-id",
        str(row.get("run_group_id")),
        "--execute",
        "--materialize-after",
    ]
    return {
        "summary": {
            "status": "ready",
            "run_group_id": row.get("run_group_id"),
            "candidate_id": row.get("candidate_id"),
            "primary_metric": row.get("primary_metric"),
            "run_count": row.get("run_count"),
            "ready_run_count": row.get("ready_run_count"),
        },
        "commands": [" ".join(command)],
        "dry_run_command": " ".join(part for part in command if part not in {"--execute", "--materialize-after"}),
        "caveats": [
            "run the dry-run command first to inspect exported scripts",
            "the execute command runs generated run.sh files and then materializes coherent machine analysis",
        ],
    }


def machine_below_export_handoff(limit: int = 10, kind: str | None = None) -> dict[str, Any]:
    """Read planned live-below export windows for residual pressure episodes."""
    payload = _analysis_artifact("machine_below_export_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "items": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    failed = [row for row in payload.get("failed_captures", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("episode_kind") == kind]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "planned_window_count": payload.get("planned_window_count", len(rows)),
            "failed_capture_count": payload.get("failed_capture_count", len(failed)),
            "root": payload.get("root"),
            "live_store": payload.get("live_store"),
            "caveats": payload.get("caveats", []),
        },
        "items": rows[:max(limit, 0)],
        "failed_captures": failed[:max(limit, 0)],
    }


def machine_experiment_manifest_diagnostics(
    limit: int = 100,
    kind: str | None = None,
    source_loadable: bool | None = None,
    controlled_valid: bool | None = None,
) -> dict[str, Any]:
    """Read raw experiment-manifest ingestion diagnostics."""
    payload = _analysis_artifact("machine_experiment_manifest_diagnostics.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "diagnostics": []}
    rows = [row for row in payload.get("diagnostics", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("manifest_kind") == kind]
    if source_loadable is not None:
        rows = [row for row in rows if bool(row.get("source_loadable")) is source_loadable]
    if controlled_valid is not None:
        rows = [row for row in rows if bool(row.get("controlled_benchmark_valid")) is controlled_valid]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "root": payload.get("root"),
            "root_exists": payload.get("root_exists"),
            "manifest_count": payload.get("manifest_count"),
            "source_loadable_count": payload.get("source_loadable_count"),
            "controlled_benchmark_valid_count": payload.get("controlled_benchmark_valid_count"),
            "validation_issue_count": payload.get("validation_issue_count"),
            "promotion_issue_count": payload.get("promotion_issue_count"),
            "controlled_run_invalid_count": payload.get("controlled_run_invalid_count"),
            "ad_hoc_observational_count": payload.get("ad_hoc_observational_count"),
            "by_kind": payload.get("by_kind", {}),
            "caveats": payload.get("caveats", []),
        },
        "diagnostics": rows[:max(limit, 0)],
    }


def machine_benchmark_readiness(
    payload_json: str | None = None,
    manifest_path: str | None = None,
    require_file_refs: bool = False,
) -> dict[str, Any]:
    """Validate a benchmark manifest payload or file without executing it."""
    import json
    from pathlib import Path

    from lynchpin.analysis.machine.controlled_benchmarks import (
        benchmark_readiness,
        validate_executed_benchmark_manifest,
    )

    path = Path(manifest_path) if manifest_path else None
    if payload_json is not None:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            return {"status": "invalid_json", "issues": [str(exc)]}
    elif path is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "invalid_json", "path": str(path), "issues": [str(exc)]}
    else:
        return {"status": "missing_input", "issues": ["provide payload_json or manifest_path"]}

    if not isinstance(payload, dict):
        return {"status": "invalid_payload", "issues": ["benchmark manifest payload must be an object"]}
    readiness = benchmark_readiness(payload).to_dict()
    validation = validate_executed_benchmark_manifest(
        payload,
        manifest_path=path,
        require_file_refs=require_file_refs,
    ).to_dict()
    return _json_safe({
        "status": "ok",
        "path": str(path) if path is not None else None,
        "readiness": readiness,
        "executed_manifest_validation": validation,
    })


def machine_derivation_inventory(limit: int = 100, project: str | None = None) -> dict[str, Any]:
    """Read fixed Nix derivation targets available for benchmark plans."""
    payload = _analysis_artifact("machine_derivation_inventory.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "targets": []}
    targets = [row for row in payload.get("targets", []) if isinstance(row, dict)]
    if project:
        targets = [row for row in targets if row.get("project") == project]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "target_count": payload.get("target_count", len(targets)),
            "ready_target_count": payload.get("ready_target_count"),
            "caveats": payload.get("caveats", []),
        },
        "targets": targets[:max(limit, 0)],
    }


def machine_support_assessments(limit: int = 25, support_level: str | None = None) -> dict[str, Any]:
    """Read support/refusal assessments for machine attribution candidates."""
    payload = _analysis_artifact("machine_support_assessment.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "assessments": []}
    rows = [row for row in payload.get("assessments", []) if isinstance(row, dict)]
    if support_level:
        rows = [row for row in rows if row.get("support_level") == support_level]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "assessment_count": payload.get("assessment_count", len(rows)),
            "refusal_count": payload.get("refusal_count"),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "natural_experiment_support_count": payload.get("natural_experiment_support_count"),
            "ready_plan_count": payload.get("ready_plan_count"),
            "run_template_count": payload.get("run_template_count"),
            "by_support_level": _count_by(rows, "support_level"),
            "caveats": payload.get("caveats", []),
        },
        "assessments": rows[:max(limit, 0)],
    }


def machine_attribution_candidate_details(candidate_id: str) -> dict[str, Any]:
    """Join candidate, plan, support, mechanism, gap, and claim rows for one candidate."""
    candidates = _analysis_artifact("machine_attribution_candidates.json") or {}
    plans = _analysis_artifact("machine_benchmark_plans.json") or {}
    support = _analysis_artifact("machine_support_assessment.json") or {}
    bundle = _analysis_artifact("machine_benchmark_manifest_bundle.json") or {}
    preflight = _analysis_artifact("machine_benchmark_preflight.json") or {}
    mechanisms = _analysis_artifact("machine_mechanism_hypotheses.json") or {}
    gaps = _analysis_artifact("machine_instrumentation_gaps.json") or {}
    claims = _analysis_artifact("machine_attribution_claims.json") or {}

    candidate = next(
        (row for row in candidates.get("candidates", []) if isinstance(row, dict) and row.get("candidate_id") == candidate_id),
        None,
    )
    assessment_rows = [
        row for row in support.get("assessments", [])
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id
    ]
    mechanism_ids = {
        str(row.get("mechanism", {}).get("mechanism_id"))
        for row in assessment_rows
        if isinstance(row.get("mechanism"), dict) and row.get("mechanism", {}).get("mechanism_id")
    }
    plan_rows = [
        row for row in plans.get("plans", [])
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id
    ]
    run_group_ids = {
        str((row.get("manifest_preview", {}).get("controlled_benchmark") or {}).get("run_group_id"))
        for row in plan_rows
        if isinstance(row.get("manifest_preview"), dict)
        and (row.get("manifest_preview", {}).get("controlled_benchmark") or {}).get("run_group_id")
    }
    return {
        "summary": {
            "status": "found" if candidate is not None or assessment_rows else "not_found",
            "candidate_id": candidate_id,
            "run_group_ids": sorted(run_group_ids),
        },
        "candidate": candidate,
        "plans": plan_rows,
        "manifest_groups": [
            row for row in bundle.get("groups", [])
            if isinstance(row, dict) and row.get("run_group_id") in run_group_ids
        ],
        "preflight_runs": [
            row for row in preflight.get("runs", [])
            if isinstance(row, dict) and row.get("run_group_id") in run_group_ids
        ],
        "support_assessments": assessment_rows,
        "mechanisms": [
            row for row in mechanisms.get("mechanisms", [])
            if isinstance(row, dict)
            and (candidate_id in row.get("candidate_ids", []) or row.get("mechanism_id") in mechanism_ids)
        ],
        "instrumentation_gaps": [
            row for row in gaps.get("gaps", [])
            if isinstance(row, dict) and row.get("candidate_id") == candidate_id
        ],
        "attribution_claims": [
            row for row in claims.get("claims", [])
            if isinstance(row, dict) and candidate_id in row.get("source_ids", [])
        ],
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value:
            label = str(value)
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _manifest_validation_status(row: dict[str, Any]) -> str | None:
    payload = row.get("manifest_validation")
    if not isinstance(payload, dict):
        return None
    if payload.get("valid") is True:
        return "valid"
    if payload.get("valid") is False:
        return "invalid"
    if "valid" in payload:
        return "unknown"
    status = payload.get("status") or payload.get("validation_status")
    return str(status) if status else None


def _count_manifest_validation_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = _manifest_validation_status(row)
        if status:
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def machine_benchmarks(
    view: str = "runs",
    limit: int = 100,
    run_group_id: str | None = None,
    workload: str | None = None,
    run_id: str | None = None,
    derivation: str | None = None,
    phase: str | None = None,
    metric: str | None = None,
    status: str | None = None,
    candidate_id: str | None = None,
    ready_only: bool = False,
    kind: str | None = None,
    source_loadable: bool | None = None,
    controlled_valid: bool | None = None,
    claim_mode: str | None = None,
) -> Any:
    """Benchmark/experiment data. view: runs, phases, estimates, claims, plans, plan_template, manifest_bundle, execution_handoff, selected_runbook, manifest_diagnostics."""
    if view == "runs":
        return machine_benchmark_runs(limit=limit, run_group_id=run_group_id, workload=workload)
    if view == "phases":
        return machine_benchmark_phases(limit=limit, run_id=run_id, derivation=derivation, phase=phase)
    if view == "estimates":
        return machine_benchmark_estimates(run_group_id=run_group_id, metric=metric)
    if view == "claims":
        return machine_experiment_claims(claim_mode=claim_mode, workload=workload, limit=limit)
    if view == "plans":
        return machine_benchmark_plans(limit=limit, status=status, run_group_id=run_group_id, candidate_id=candidate_id)
    if view == "plan_template":
        return machine_benchmark_plan_template(candidate_id=candidate_id or "")
    if view == "manifest_bundle":
        return machine_benchmark_manifest_bundle(limit=limit)
    if view == "execution_handoff":
        return machine_benchmark_execution_handoff(limit=limit, ready_only=ready_only)
    if view == "selected_runbook":
        return machine_benchmark_selected_runbook(run_group_id=run_group_id, candidate_id=candidate_id)
    if view == "manifest_diagnostics":
        return machine_experiment_manifest_diagnostics(limit=limit, kind=kind, source_loadable=source_loadable, controlled_valid=controlled_valid)
    return {"error": f"unknown view {view!r}. choices: runs, phases, estimates, claims, plans, plan_template, manifest_bundle, execution_handoff, selected_runbook, manifest_diagnostics"}


def machine_validation_design(
    view: str = "summary",
    limit: int = 100,
    boundary_type: str | None = None,
    project: str | None = None,
) -> Any:
    """Validation design data. view: summary (boundaries + split), splits (discovery/validation splits), boundaries (boundary candidates)."""
    _payload = _analysis_artifact("machine_validation_design.json")
    if view == "summary":
        if _payload is None:
            return {"summary": {"status": "missing"}, "split": None, "boundaries": []}
        boundaries = [row for row in _payload.get("boundaries", []) if isinstance(row, dict)]
        return {
            "summary": {
                "generated_at_utc": _payload.get("generated_at_utc"),
                "generated_for": _payload.get("generated_for"),
                "boundary_count": _payload.get("boundary_count", len(boundaries)),
                "caveats": _payload.get("caveats", []),
            },
            "split": _payload.get("split"),
            "boundaries": boundaries[:max(limit, 0)],
        }
    if view == "splits":
        return machine_discovery_validation_splits(project=project)
    if view == "boundaries":
        return machine_boundary_candidates(limit=limit, boundary_type=boundary_type, project=project)
    return {"error": f"unknown view {view!r}. choices: summary, splits, boundaries"}


def machine_matched(
    view: str = "designs",
    limit: int = 100,
    status: str | None = None,
    candidate_id: str | None = None,
    boundary_id: str | None = None,
    signal: str | None = None,
) -> Any:
    """Matched experiment data. view: designs, comparisons, negative_controls, signal_comparisons."""
    if view == "designs":
        return machine_matched_designs(limit=limit, status=status)
    if view == "comparisons":
        return machine_matched_comparisons(limit=limit, candidate_id=candidate_id, boundary_id=boundary_id)
    if view == "negative_controls":
        return machine_negative_controls(limit=limit, status=status, boundary_id=boundary_id)
    if view == "signal_comparisons":
        return machine_comparisons(limit=limit, signal=signal)
    return {"error": f"unknown view {view!r}. choices: designs, comparisons, negative_controls, signal_comparisons"}
