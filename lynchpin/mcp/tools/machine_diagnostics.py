"""Machine diagnostics, assumption checks, mechanism hypotheses, and attribution tools."""
from typing import Any

from lynchpin.mcp.tools._machine_helpers import _analysis_artifact


def machine_dataset_diagnostics(kind: str | None = None, severity: str | None = None) -> dict[str, Any]:
    """Read extant machine/work dataset mining diagnostics."""
    payload = _analysis_artifact("machine_dataset_diagnostics.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "diagnostics": []}
    rows = [row for row in payload.get("diagnostics", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("diagnostic_kind") == kind]
    if severity:
        rows = [row for row in rows if row.get("severity") == severity]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "diagnostic_count": payload.get("diagnostic_count", len(rows)),
            "feature_status": (payload.get("feature_audit") or {}).get("status")
            if isinstance(payload.get("feature_audit"), dict)
            else None,
            "multiplicity_status": (payload.get("mining_audit") or {}).get("multiplicity_status")
            if isinstance(payload.get("mining_audit"), dict)
            else None,
            "caveats": payload.get("caveats", []),
        },
        "feature_audit": payload.get("feature_audit"),
        "mining_audit": payload.get("mining_audit"),
        "diagnostics": rows,
    }


def machine_instrumentation_gaps(
    limit: int = 50,
    project: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Read instrumentation gaps that block machine attribution support upgrades."""
    payload = _analysis_artifact("machine_instrumentation_gaps.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "gaps": []}
    rows = [row for row in payload.get("gaps", []) if isinstance(row, dict)]
    if project:
        rows = [row for row in rows if row.get("project") == project]
    if source:
        rows = [row for row in rows if row.get("missing_source") == source]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "gap_count": payload.get("gap_count", len(rows)),
            "by_missing_source": payload.get("by_missing_source", {}),
            "by_mechanism_family": payload.get("by_mechanism_family", {}),
            "caveats": payload.get("caveats", []),
        },
        "gaps": rows[:max(limit, 0)],
    }


def machine_attribution_claims(
    limit: int = 25,
    support_level: str | None = None,
    project: str | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    """Read promoted machine attribution claim/refusal ledger rows."""
    payload = _analysis_artifact("machine_attribution_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "claims": []}
    rows = [row for row in payload.get("claims", []) if isinstance(row, dict)]
    if support_level:
        rows = [row for row in rows if row.get("support_level") == support_level]
    if project:
        rows = [row for row in rows if row.get("project") == project]
    if metric:
        rows = [row for row in rows if (row.get("payload") or {}).get("metric") == metric]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "claim_count": payload.get("claim_count", len(rows)),
            "by_support_level": payload.get("by_support_level", {}),
            "filters": {"support_level": support_level, "project": project, "metric": metric},
            "caveats": payload.get("caveats", []),
        },
        "claims": rows[:max(limit, 0)],
    }


def machine_mechanism_hypotheses(
    limit: int = 25,
    family: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Read falsifiable mechanism hypotheses grouped from support assessments."""
    payload = _analysis_artifact("machine_mechanism_hypotheses.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "mechanisms": []}
    rows = [row for row in payload.get("mechanisms", []) if isinstance(row, dict)]
    if family:
        rows = [row for row in rows if row.get("mechanism_family") == family]
    if candidate_id:
        rows = [row for row in rows if candidate_id in row.get("candidate_ids", [])]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "mechanism_count": payload.get("mechanism_count", len(rows)),
            "caveats": payload.get("caveats", []),
        },
        "mechanisms": rows[:max(limit, 0)],
    }


def machine_claim_evidence(claim_id: str) -> dict[str, Any]:
    """Join one machine attribution claim to assumptions and upstream evidence ids."""
    claims = _analysis_artifact("machine_attribution_claims.json") or {}
    assumptions = _analysis_artifact("machine_assumption_checks.json") or {}
    gaps = _analysis_artifact("machine_instrumentation_gaps.json") or {}
    support = _analysis_artifact("machine_support_assessment.json") or {}
    matched = _analysis_artifact("machine_matched_designs.json") or {}
    negative = _analysis_artifact("machine_negative_controls.json") or {}
    claim = next(
        (row for row in claims.get("claims", []) if isinstance(row, dict) and row.get("claim_id") == claim_id),
        None,
    )
    source_ids = set(claim.get("source_ids", [])) if isinstance(claim, dict) else set()
    matched_designs = [
        row for row in matched.get("designs", [])
        if isinstance(row, dict) and row.get("design_id") in source_ids
    ]
    matched_design_ids = {
        str(row.get("design_id"))
        for row in matched_designs
        if row.get("design_id")
    }
    return {
        "summary": {"status": "found" if claim is not None else "not_found", "claim_id": claim_id},
        "claim": claim,
        "assumption_checks": [
            row for row in assumptions.get("checks", [])
            if isinstance(row, dict) and row.get("claim_id") == claim_id
        ],
        "instrumentation_gaps": [
            row for row in gaps.get("gaps", [])
            if isinstance(row, dict) and row.get("candidate_id") in source_ids
        ],
        "support_assessments": [
            row for row in support.get("assessments", [])
            if isinstance(row, dict)
            and (row.get("assessment_id") in source_ids or row.get("candidate_id") in source_ids)
        ],
        "matched_designs": matched_designs,
        "negative_controls": [
            row for row in negative.get("controls", [])
            if isinstance(row, dict)
            and (
                row.get("control_id") in source_ids
                or row.get("design_id") in source_ids
                or row.get("design_id") in matched_design_ids
            )
        ],
        "source_ids": sorted(str(item) for item in source_ids),
    }


def machine_assumption_checks(limit: int = 50, status: str | None = None, claim_id: str | None = None) -> dict[str, Any]:
    """Read assumption checks limiting or supporting machine attribution claims."""
    payload = _analysis_artifact("machine_assumption_checks.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "checks": []}
    rows = [row for row in payload.get("checks", []) if isinstance(row, dict)]
    if status:
        rows = [row for row in rows if row.get("check_status") == status]
    if claim_id:
        rows = [row for row in rows if row.get("claim_id") == claim_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "check_count": payload.get("check_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "checks": rows[:max(limit, 0)],
    }


def _machine_attribution_summary(
    view: str = "candidates",
    limit: int = 25,
    validation_status: str | None = None,
    mechanism_family: str | None = None,
    pareto_frontier: bool | None = None,
    support_level: str | None = None,
    project: str | None = None,
) -> Any:
    """Machine attribution data. view: candidates, assessments, derivations."""
    from lynchpin.mcp.tools.machine_benchmarks import (
        machine_attribution_candidates,
        machine_derivation_inventory,
        machine_support_assessments,
    )

    if view == "candidates":
        return machine_attribution_candidates(limit=limit, validation_status=validation_status, mechanism_family=mechanism_family, pareto_frontier=pareto_frontier)
    if view == "assessments":
        return machine_support_assessments(limit=limit, support_level=support_level)
    if view == "derivations":
        return machine_derivation_inventory(limit=limit, project=project)
    return {"error": f"unknown view {view!r}. choices: candidates, assessments, derivations"}


def machine_attribution(
    view: str = "summary",
    project: str | None = None,
    candidate_id: str | None = None,
    limit: int = 25,
    validation_status: str | None = None,
    mechanism_family: str | None = None,
    pareto_frontier: bool | None = None,
    support_level: str | None = None,
    metric: str | None = None,
) -> Any:
    """Machine attribution data. view: summary (overall attribution summary), candidates (attribution candidate details for one candidate_id), claims (attribution claims with evidence)."""
    from lynchpin.mcp.tools.machine_benchmarks import machine_attribution_candidate_details

    if view == "summary":
        return _machine_attribution_summary(
            view="candidates",
            limit=limit,
            validation_status=validation_status,
            mechanism_family=mechanism_family,
            pareto_frontier=pareto_frontier,
            support_level=support_level,
            project=project,
        )
    if view == "candidates":
        return machine_attribution_candidate_details(candidate_id=candidate_id or "")
    if view == "claims":
        return machine_attribution_claims(limit=limit, support_level=support_level, project=project, metric=metric)
    return {"error": f"unknown view {view!r}. choices: summary, candidates, claims"}
