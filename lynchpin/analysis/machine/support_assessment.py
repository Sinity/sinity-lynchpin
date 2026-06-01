"""Support/refusal assessment for machine attribution candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_if_exists, load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineMechanismHypothesis:
    mechanism_id: str
    mechanism_family: str
    expected_signatures: tuple[str, ...]
    falsifiers: tuple[str, ...]
    discriminating_measurements: tuple[str, ...]
    current_support_ceiling: str
    cheapest_next_action: str


@dataclass(frozen=True)
class MachineInstrumentationGap:
    gap_id: str
    candidate_id: str
    missing: str
    why_it_matters: str
    next_action: str


@dataclass(frozen=True)
class MachineSupportAssessment:
    assessment_id: str
    candidate_id: str
    project: str | None
    metric: str
    suspected_factor: str
    mechanism: MachineMechanismHypothesis
    support_level: str
    confidence: float
    decision: str
    refusal_reasons: tuple[str, ...]
    instrumentation_gaps: tuple[MachineInstrumentationGap, ...]
    source_artifacts: tuple[str, ...]
    source_ids: tuple[str, ...]
    summary: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineSupportAssessmentAnalysis:
    generated_for: dict[str, Any]
    assessment_count: int
    refusal_count: int
    candidate_count: int
    controlled_claim_count: int
    natural_experiment_support_count: int
    ready_plan_count: int
    run_template_count: int
    dataset_feature_status: str | None
    dataset_multiplicity_status: str | None
    assessments: list[MachineSupportAssessment]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_support_assessment(
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    plans_path: Path | None = None,
    manifest_bundle_path: Path | None = None,
    claims_path: Path | None = None,
    dataset_diagnostics_path: Path | None = None,
    matched_designs_path: Path | None = None,
    negative_controls_path: Path | None = None,
    limit: int = 25,
) -> MachineSupportAssessmentAnalysis:
    candidates_payload = load_json_object(
        candidates_path or resolve_analysis_path("machine_attribution_candidates.json"),
        label="machine attribution candidates",
    )
    plans_payload = load_json_object(
        plans_path or resolve_analysis_path("machine_benchmark_plans.json"),
        label="machine benchmark plans",
    )
    manifest_bundle_payload = _optional_payload(
        manifest_bundle_path,
        default_name="machine_benchmark_manifest_bundle.json",
    )
    claims_payload = _optional_payload(claims_path, default_name="machine_experiment_claims.json")
    dataset_payload = _optional_payload(dataset_diagnostics_path, default_name="machine_dataset_diagnostics.json")
    matched_payload = _optional_payload(matched_designs_path, default_name="machine_matched_designs.json")
    negative_payload = _optional_payload(negative_controls_path, default_name="machine_negative_controls.json")
    dataset_gate = _dataset_gate(dataset_payload)
    candidates = [row for row in candidates_payload.get("candidates", []) if isinstance(row, dict)]
    if limit > 0:
        candidates = candidates[:limit]
    plans_by_candidate = _plans_by_candidate(plans_payload)
    controlled_claim_count = int((claims_payload or {}).get("controlled_claim_count") or 0)
    controlled_run_groups = _controlled_run_groups(claims_payload)
    matched_designs = _matched_designs_by_id(matched_payload)
    negative_controls = _negative_controls_by_design(negative_payload)
    run_template_count = int((manifest_bundle_payload or {}).get("run_template_count") or 0)
    assessments = [
        _assessment_for_candidate(
            candidate,
            plans=plans_by_candidate.get(str(candidate.get("candidate_id") or ""), []),
            run_template_count=run_template_count,
            controlled_claim_count=controlled_claim_count,
            controlled_run_groups=controlled_run_groups,
            matched_designs=matched_designs,
            negative_controls=negative_controls,
            dataset_gate=dataset_gate,
        )
        for candidate in candidates
    ]
    return MachineSupportAssessmentAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": [
                "machine_attribution_candidates.json",
                "machine_benchmark_plans.json",
                "machine_benchmark_manifest_bundle.json",
                "machine_experiment_claims.json",
                "machine_dataset_diagnostics.json",
                "machine_matched_designs.json",
                "machine_negative_controls.json",
            ],
            "limit": limit,
        },
        assessment_count=len(assessments),
        refusal_count=sum(1 for row in assessments if row.support_level == "insufficient"),
        candidate_count=len(candidates),
        controlled_claim_count=controlled_claim_count,
        natural_experiment_support_count=sum(
            1 for row in assessments if row.support_level == "natural_experiment"
        ),
        ready_plan_count=int(plans_payload.get("ready_plan_count") or 0),
        run_template_count=run_template_count,
        dataset_feature_status=dataset_gate.get("feature_status"),
        dataset_multiplicity_status=dataset_gate.get("multiplicity_status"),
        assessments=assessments,
        caveats=[
            "support assessments are gatekeeping artifacts; they do not execute benchmarks",
            "insufficient support is an explicit refusal, not a failed analysis",
        ],
    )


def write_machine_support_assessment(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    plans_path: Path | None = None,
    manifest_bundle_path: Path | None = None,
    claims_path: Path | None = None,
    dataset_diagnostics_path: Path | None = None,
    matched_designs_path: Path | None = None,
    negative_controls_path: Path | None = None,
    limit: int = 25,
) -> MachineSupportAssessmentAnalysis:
    analysis = analyze_machine_support_assessment(
        start=start,
        end=end,
        candidates_path=candidates_path,
        plans_path=plans_path,
        manifest_bundle_path=manifest_bundle_path,
        claims_path=claims_path,
        dataset_diagnostics_path=dataset_diagnostics_path,
        matched_designs_path=matched_designs_path,
        negative_controls_path=negative_controls_path,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _assessment_for_candidate(
    candidate: dict[str, Any],
    *,
    plans: list[dict[str, Any]],
    run_template_count: int,
    controlled_claim_count: int,
    controlled_run_groups: set[str],
    matched_designs: dict[str, dict[str, Any]],
    negative_controls: dict[str, list[dict[str, Any]]],
    dataset_gate: dict[str, Any],
) -> MachineSupportAssessment:
    candidate_id = str(candidate.get("candidate_id") or "candidate")
    mechanism = _mechanism_for_candidate(candidate)
    plan = plans[0] if plans else {}
    readiness = plan.get("readiness") if isinstance(plan.get("readiness"), dict) else {}
    required = tuple(str(item) for item in plan.get("required_bindings", ()) if item) if isinstance(plan, dict) else ()
    natural_gate = _natural_experiment_gate(
        candidate,
        matched_designs=matched_designs,
        negative_controls=negative_controls,
        dataset_gate=dataset_gate,
    )
    refusal_reasons = _refusal_reasons(
        candidate=candidate,
        plan=plan,
        readiness=readiness,
        run_template_count=run_template_count,
        controlled_claim_count=controlled_claim_count,
        controlled_run_groups=controlled_run_groups,
        natural_gate=natural_gate,
        dataset_gate=dataset_gate,
    )
    support_level = _support_level(refusal_reasons, natural_gate=natural_gate)
    decision = "refuse_claim" if refusal_reasons else "promote_claim_candidate"
    gaps = tuple(
        _gap(candidate_id, missing=missing)
        for missing in _missing_gaps(required=required, natural_gate=natural_gate)
    ) if refusal_reasons else ()
    metric = str(candidate.get("metric") or "unknown_metric")
    factor = str(candidate.get("suspected_factor") or "unknown_factor")
    return MachineSupportAssessment(
        assessment_id=_assessment_id(candidate_id, metric, factor),
        candidate_id=candidate_id,
        project=str(candidate.get("project")) if candidate.get("project") else None,
        metric=metric,
        suspected_factor=factor,
        mechanism=mechanism,
        support_level=support_level,
        confidence=_confidence(support_level),
        decision=decision,
        refusal_reasons=tuple(refusal_reasons),
        instrumentation_gaps=gaps,
        source_artifacts=_source_artifacts(candidate, natural_gate=natural_gate),
        source_ids=_source_ids(candidate, natural_gate=natural_gate),
        summary=(
            f"Refuse causal claim for {factor}: {refusal_reasons[0]}"
            if refusal_reasons
            else _support_summary(factor, support_level)
        ),
        caveats=tuple(str(item) for item in candidate.get("caveats", ()) if item),
    )


def _mechanism_for_candidate(candidate: dict[str, Any]) -> MachineMechanismHypothesis:
    family = str(candidate.get("mechanism_family") or "unknown_mined_stage_slowdown")
    factor = str(candidate.get("suspected_factor") or "")
    if "pressure" in factor or "contention" in family:
        key = "resource_contention"
        expected = (
            "duration increases during CPU/IO/memory pressure windows",
            "machine episodes overlap the affected work windows",
            "unrelated stages show weaker or absent deltas",
        )
        falsifiers = (
            "effect remains after excluding pressure windows",
            "quiet-window controls show the same delta",
            "pressure telemetry is missing or temporally after the outcome",
        )
        measurements = ("pre-window PSI", "during-window PSI", "below/cgroup attribution", "negative-control stages")
        next_action = "bind fixed derivations and run randomized warm/cold benchmark under quiet and pressure-like conditions"
    elif "cohort" in factor or "stage" in family:
        key = "stage_regression_or_workload_mix"
        expected = (
            "the same stage remains slower in validation windows",
            "slowdown concentrates in specific command/project/stage cohorts",
            "phase logs identify whether time is eval, build, test, or setup",
        )
        falsifiers = (
            "held-out windows lose the contrast",
            "matched controls with the same command/project remove the delta",
            "Nix internal-json phase timing attributes delay to unrelated setup",
        )
        measurements = ("Nix internal-json phase timing", "matched command/project controls", "held-out validation split")
        next_action = "capture derivation-bound internal-json logs and run matched or controlled stage replay"
    else:
        key = "unknown_machine_or_workload_mechanism"
        expected = ("candidate repeats across observations", "controlled plan can isolate treatment",)
        falsifiers = ("candidate disappears in validation", "no fixed workload can reproduce the surface",)
        measurements = ("validation split", "fixed derivation benchmark", "telemetry overlap")
        next_action = "instrument the missing surface before upgrading support"
    return MachineMechanismHypothesis(
        mechanism_id=f"machine-mechanism:{key}",
        mechanism_family=key,
        expected_signatures=expected,
        falsifiers=falsifiers,
        discriminating_measurements=measurements,
        current_support_ceiling="candidate",
        cheapest_next_action=next_action,
    )


def _refusal_reasons(
    *,
    candidate: dict[str, Any],
    plan: dict[str, Any],
    readiness: dict[str, Any],
    run_template_count: int,
    controlled_claim_count: int,
    controlled_run_groups: set[str],
    natural_gate: dict[str, Any],
    dataset_gate: dict[str, Any],
) -> list[str]:
    reasons = []
    if natural_gate.get("eligible"):
        return list(dict.fromkeys(str(issue) for issue in natural_gate.get("issues", ()) if issue))

    run_group_id = _plan_run_group_id(plan)
    has_candidate_controlled_claim = run_group_id is not None and run_group_id in controlled_run_groups
    if not has_candidate_controlled_claim:
        if plan.get("planning_status") == "ready" and run_template_count > 0:
            reasons.append("ready benchmark manifest templates exist but no executed controlled benchmark claim exists for candidate")
        elif controlled_claim_count > 0:
            reasons.append("executed controlled benchmark claims exist only for other run groups")
        else:
            reasons.append("no executed controlled benchmark claim exists")
    if not plan:
        reasons.append("no benchmark plan exists for candidate")
    elif plan.get("planning_status") != "ready":
        required = ", ".join(str(item) for item in plan.get("required_bindings", ()) if item)
        reasons.append(f"benchmark plan is not ready: {required or 'unresolved readiness gaps'}")
    if readiness and readiness.get("controlled") is not True:
        for issue in readiness.get("issues", ()):
            reasons.append(f"controlled benchmark contract gap: {issue}")
    if candidate.get("support_ceiling") == "candidate" and not has_candidate_controlled_claim:
        reasons.append("source candidate support ceiling is candidate")
    for issue in dataset_gate.get("issues", ()):
        reasons.append(str(issue))
    return list(dict.fromkeys(reasons))


def _support_level(refusal_reasons: list[str], *, natural_gate: dict[str, Any]) -> str:
    if refusal_reasons:
        return "insufficient"
    if natural_gate.get("eligible"):
        return "natural_experiment"
    return "controlled"


def _confidence(support_level: str) -> float:
    if support_level == "insufficient":
        return 0.9
    if support_level == "natural_experiment":
        return 0.6
    return 0.7


def _support_summary(factor: str, support_level: str) -> str:
    if support_level == "natural_experiment":
        return f"Natural-experiment design support available for {factor}"
    return f"Controlled support available for {factor}"


def _missing_gaps(*, required: tuple[str, ...], natural_gate: dict[str, Any]) -> tuple[str, ...]:
    missing = tuple(str(item) for item in natural_gate.get("missing", ()) if item)
    if missing:
        return missing
    return required or ("executed_controlled_run",)


def _source_artifacts(candidate: dict[str, Any], *, natural_gate: dict[str, Any]) -> tuple[str, ...]:
    artifacts = [str(item) for item in candidate.get("source_artifacts", ()) if item]
    artifacts.extend(str(item) for item in natural_gate.get("source_artifacts", ()) if item)
    return tuple(dict.fromkeys(artifacts))


def _source_ids(candidate: dict[str, Any], *, natural_gate: dict[str, Any]) -> tuple[str, ...]:
    ids = [str(item) for item in candidate.get("source_ids", ()) if item]
    ids.extend(str(item) for item in natural_gate.get("source_ids", ()) if item)
    return tuple(dict.fromkeys(ids))


def _natural_experiment_gate(
    candidate: dict[str, Any],
    *,
    matched_designs: dict[str, dict[str, Any]],
    negative_controls: dict[str, list[dict[str, Any]]],
    dataset_gate: dict[str, Any],
) -> dict[str, Any]:
    if candidate.get("support_ceiling") != "natural_experiment_design":
        return {"eligible": False, "ready": False, "issues": [], "missing": []}

    issues: list[str] = []
    missing: list[str] = []
    source_ids = tuple(str(item) for item in candidate.get("source_ids", ()) if item)
    designs = [matched_designs[source_id] for source_id in source_ids if source_id in matched_designs]
    if not designs:
        issues.append("no matched natural-experiment design found for candidate source_ids")
        missing.append("matched_design")
        return _natural_gate_payload(issues, missing, source_ids=source_ids)

    design = _best_natural_design(designs)
    design_id = str(design.get("design_id") or "")
    if design.get("identification_status") != "design_ready":
        issues.append(f"matched design identification is {design.get('identification_status') or 'unknown'}")
        missing.append("design_ready_identification")
    if design.get("support_ceiling") != "natural_experiment_design":
        issues.append(f"matched design support ceiling is {design.get('support_ceiling') or 'unknown'}")
        missing.append("natural_experiment_support_ceiling")

    controls = negative_controls.get(design_id, [])
    if not controls:
        issues.append("negative-control checks missing for matched design")
        missing.append("negative_controls")
    else:
        failed = [row for row in controls if row.get("status") == "failed"]
        required = [row for row in controls if row.get("support_required") is not False]
        required_unavailable = [row for row in required if row.get("status") != "passed"]
        if failed:
            issues.append("one or more negative-control checks failed for matched design")
            missing.append("resolved_negative_controls")
        elif required_unavailable:
            issues.append("one or more support-required negative-control checks are unavailable for matched design")
            missing.append("complete_negative_controls")

    for issue in dataset_gate.get("issues", ()):
        issues.append(str(issue))
        missing.append("dataset_diagnostics_ready")

    return _natural_gate_payload(
        issues,
        missing,
        source_ids=(design_id, *source_ids),
        source_artifacts=("machine_matched_designs.json", "machine_negative_controls.json"),
    )


def _natural_gate_payload(
    issues: list[str],
    missing: list[str],
    *,
    source_ids: tuple[str, ...],
    source_artifacts: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "eligible": True,
        "ready": not issues,
        "issues": list(dict.fromkeys(issues)),
        "missing": list(dict.fromkeys(missing)),
        "source_ids": tuple(dict.fromkeys(source_ids)),
        "source_artifacts": source_artifacts,
    }


def _best_natural_design(designs: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        designs,
        key=lambda row: (
            row.get("identification_status") != "design_ready",
            row.get("support_ceiling") != "natural_experiment_design",
            -abs(float(row.get("difference_in_differences") or 0.0)),
            str(row.get("design_id") or ""),
        ),
    )[0]


def _controlled_run_groups(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    groups = set()
    for row in payload.get("claim_packs", ()):
        if not isinstance(row, dict):
            continue
        if row.get("claim_mode") != "controlled_benchmark":
            continue
        run_group_id = row.get("run_group_id")
        if run_group_id:
            groups.add(str(run_group_id))
    return groups


def _matched_designs_by_id(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    designs = {}
    for row in payload.get("designs", ()):
        if not isinstance(row, dict):
            continue
        design_id = row.get("design_id")
        if design_id:
            designs[str(design_id)] = row
    return designs


def _negative_controls_by_design(payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    controls: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("controls", ()):
        if not isinstance(row, dict):
            continue
        design_id = row.get("design_id")
        if design_id:
            controls.setdefault(str(design_id), []).append(row)
    return controls


def _plan_run_group_id(plan: dict[str, Any]) -> str | None:
    preview = plan.get("manifest_preview") if isinstance(plan.get("manifest_preview"), dict) else {}
    controlled = preview.get("controlled_benchmark") if isinstance(preview.get("controlled_benchmark"), dict) else {}
    value = controlled.get("run_group_id") or plan.get("run_group_id")
    return str(value) if value else None


def _dataset_gate(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"feature_status": None, "multiplicity_status": None, "issues": []}
    feature = payload.get("feature_audit") if isinstance(payload.get("feature_audit"), dict) else {}
    mining = payload.get("mining_audit") if isinstance(payload.get("mining_audit"), dict) else {}
    feature_status = str(feature.get("status") or "unknown")
    multiplicity_status = str(mining.get("multiplicity_status") or "unknown")
    issues = []
    if feature_status not in {"ready_for_mining"}:
        issues.append(f"extant dataset feature audit is {feature_status}")
    if multiplicity_status not in {"registered"}:
        issues.append(f"extant dataset search-space audit is {multiplicity_status}")
    return {
        "feature_status": feature_status,
        "multiplicity_status": multiplicity_status,
        "issues": issues,
    }


def _gap(candidate_id: str, *, missing: str) -> MachineInstrumentationGap:
    return MachineInstrumentationGap(
        gap_id=f"machine-gap:{hashlib.sha1((candidate_id + ':' + missing).encode()).hexdigest()[:16]}",
        candidate_id=candidate_id,
        missing=missing,
        why_it_matters=_gap_reason(missing),
        next_action=_gap_action(missing),
    )


def _gap_reason(missing: str) -> str:
    return {
        "fixed_derivation_set": "without a fixed derivation set, repeated runs may test different workloads",
        "executed_controlled_run": "without executed randomized runs, no controlled estimate exists",
        "negative_controls": "without negative-control checks, shared shocks and placebo movement cannot be ruled out",
        "resolved_negative_controls": "failed negative controls threaten the natural-experiment identification strategy",
        "complete_negative_controls": "unavailable negative controls leave the natural-experiment design under-tested",
    }.get(missing, f"{missing} is required by the controlled benchmark contract")


def _gap_action(missing: str) -> str:
    return {
        "fixed_derivation_set": "bind concrete drv_path/store_path entries in the benchmark plan",
        "executed_controlled_run": "execute the approved manifest and promote run logs/telemetry",
        "negative_controls": "generate negative-control checks for the matched design before upgrading support",
        "resolved_negative_controls": "inspect failed controls and add sensitivity analysis or demote the design",
        "complete_negative_controls": "collect or derive the missing placebo/control check for the matched design",
    }.get(missing, "complete the missing benchmark-plan binding")


def _plans_by_candidate(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for plan in payload.get("plans", []):
        if not isinstance(plan, dict):
            continue
        preview = plan.get("manifest_preview") if isinstance(plan.get("manifest_preview"), dict) else {}
        candidate = preview.get("candidate") if isinstance(preview.get("candidate"), dict) else {}
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id:
            result.setdefault(candidate_id, []).append(plan)
    return result


def _optional_payload(path: Path | None, *, default_name: str) -> dict[str, Any] | None:
    loaded = load_json_if_exists(path or resolve_analysis_path(default_name))
    return loaded if isinstance(loaded, dict) else None


def _assessment_id(*parts: Any) -> str:
    raw = "\0".join(str(part) for part in parts)
    return f"machine-assessment:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineInstrumentationGap",
    "MachineMechanismHypothesis",
    "MachineSupportAssessment",
    "MachineSupportAssessmentAnalysis",
    "analyze_machine_support_assessment",
    "write_machine_support_assessment",
]
