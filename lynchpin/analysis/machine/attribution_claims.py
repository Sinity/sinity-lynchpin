"""Typed machine attribution claims backed by the generic claim substrate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from lynchpin.core.io import load_json_if_exists, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime
from lynchpin.core.projects import canonical_project_name
from lynchpin.substrate.claims import AnalysisClaimRow, claim_id

SupportLevel = Literal[
    "controlled",
    "natural_experiment",
    "observational",
    "insufficient",
]


@dataclass(frozen=True)
class MachineAttributionClaim:
    claim_type: str
    project: str | None
    date: date | None
    metric: str
    effect_kind: str
    support_level: SupportLevel
    confidence: float
    summary: str
    baseline: dict[str, Any]
    comparison: dict[str, Any]
    estimate: dict[str, Any]
    source_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def to_analysis_claim(self) -> AnalysisClaimRow:
        _validate_confidence(self.confidence)
        score = _estimate_score(self.estimate)
        return AnalysisClaimRow(
            claim_id=claim_id(
                "machine_attribution",
                self.project,
                self.date,
                self.metric,
                self.effect_kind,
                self.summary,
                *self.source_ids,
            ),
            claim_type=self.claim_type,
            project=self.project,
            date=self.date,
            support_level=self.support_level,
            confidence=self.confidence,
            score=score,
            summary=self.summary,
            source_ids=self.source_ids,
            relation_ids=(),
            caveats=self.caveats,
            payload={
                "metric": self.metric,
                "effect_kind": self.effect_kind,
                "baseline": self.baseline,
                "comparison": self.comparison,
                "estimate": self.estimate,
            },
        )


@dataclass(frozen=True)
class MachineAttributionClaimAnalysis:
    generated_for: dict[str, Any]
    claim_count: int
    by_support_level: dict[str, int]
    claims: list[dict[str, Any]]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_attribution_claims(
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
    experiment_claims_path: Path | None = None,
    matched_designs_path: Path | None = None,
    negative_controls_path: Path | None = None,
) -> MachineAttributionClaimAnalysis:
    support_payload = _optional_payload(support_assessment_path, "machine_support_assessment.json")
    experiment_payload = _optional_payload(experiment_claims_path, "machine_experiment_claims.json")
    matched_payload = _optional_payload(matched_designs_path, "machine_matched_designs.json")
    negative_payload = _optional_payload(negative_controls_path, "machine_negative_controls.json")
    rows = [
        *(_claim_row(row) for row in _support_rows(
            support_payload,
            matched_payload=matched_payload,
            negative_payload=negative_payload,
        )),
        *(_claim_row(row) for row in _controlled_estimate_claims(experiment_payload)),
    ]
    rows = [row for row in rows if row is not None]
    by_support: dict[str, int] = {}
    for row in rows:
        by_support[str(row.support_level or "unknown")] = by_support.get(str(row.support_level or "unknown"), 0) + 1
    return MachineAttributionClaimAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": [
                "machine_support_assessment.json",
                "machine_experiment_claims.json",
                "machine_matched_designs.json",
                "machine_negative_controls.json",
            ],
        },
        claim_count=len(rows),
        by_support_level=dict(sorted(by_support.items())),
        claims=[_claim_payload(row) for row in rows],
        caveats=[
            "claim artifact is deterministic over support assessments and experiment claim packs",
            "insufficient rows are explicit refusals; they are not failed analyses",
        ],
    )


def write_machine_attribution_claims(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
    experiment_claims_path: Path | None = None,
    matched_designs_path: Path | None = None,
    negative_controls_path: Path | None = None,
) -> MachineAttributionClaimAnalysis:
    analysis = analyze_machine_attribution_claims(
        start=start,
        end=end,
        support_assessment_path=support_assessment_path,
        experiment_claims_path=experiment_claims_path,
        matched_designs_path=matched_designs_path,
        negative_controls_path=negative_controls_path,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _support_rows(
    payload: dict[str, Any] | None,
    *,
    matched_payload: dict[str, Any] | None,
    negative_payload: dict[str, Any] | None,
) -> tuple[MachineAttributionClaim, ...]:
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("assessments")
    if not isinstance(rows, list):
        return ()
    matched_designs = _matched_designs_by_id(matched_payload)
    negative_controls = _negative_controls_by_design(negative_payload)
    claims = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        support_level = str(row.get("support_level") or "insufficient")
        if support_level not in {"controlled", "natural_experiment", "observational", "insufficient"}:
            support_level = "insufficient"
        metric = str(row.get("metric") or "unknown_metric")
        factor = str(row.get("suspected_factor") or "unknown_factor")
        support_source_ids = tuple(
            str(value)
            for value in (
                row.get("assessment_id"),
                row.get("candidate_id"),
                *(_list(row.get("source_ids"))),
            )
            if value
        )
        estimate = _support_estimate_payload(
            row,
            support_level=support_level,
            source_ids=support_source_ids,
            matched_designs=matched_designs,
            negative_controls=negative_controls,
        )
        claims.append(MachineAttributionClaim(
            claim_type="machine_attribution",
            project=canonical_project_name(row.get("project")) if row.get("project") else None,
            date=None,
            metric=metric,
            effect_kind=factor,
            support_level=support_level,  # type: ignore[arg-type]
            confidence=_confidence(row.get("confidence")),
            summary=str(row.get("summary") or f"{support_level} support for {factor}"),
            baseline={"candidate_id": row.get("candidate_id")},
            comparison={"decision": row.get("decision"), "support_level": support_level},
            estimate=estimate,
            source_ids=tuple(dict.fromkeys(support_source_ids)),
            caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
        ))
    return tuple(claims)


def _support_estimate_payload(
    row: dict[str, Any],
    *,
    support_level: str,
    source_ids: tuple[str, ...],
    matched_designs: dict[str, dict[str, Any]],
    negative_controls: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    design = next((matched_designs[source_id] for source_id in source_ids if source_id in matched_designs), None)
    controls = negative_controls.get(str(design.get("design_id") or ""), []) if design else []
    metric = str(row.get("metric") or (design or {}).get("outcome_metric") or "unknown_metric")
    design_caveats = _string_list((design or {}).get("caveats"))
    assessment_caveats = _string_list(row.get("caveats"))
    negative_statuses = tuple(str(control.get("status") or "unknown") for control in controls)
    payload: dict[str, Any] = {
        "support_level": support_level,
        "support_ceiling": (design or {}).get("support_ceiling") or row.get("support_level"),
        "refusal_reasons": row.get("refusal_reasons") if isinstance(row.get("refusal_reasons"), list) else [],
        "instrumentation_gaps": row.get("instrumentation_gaps")
        if isinstance(row.get("instrumentation_gaps"), list)
        else [],
        "source_artifacts": row.get("source_artifacts") if isinstance(row.get("source_artifacts"), list) else [],
        "estimand": "boundary effect on the primary outcome"
        if design else "support assessment outcome for the candidate",
        "estimator": "matched median difference-in-differences"
        if design else "support assessor gate",
        "unit_of_analysis": "work_observation_stage" if metric.startswith("stage.") else "machine_or_work_observation",
        "primary_metric": metric,
        "confidence_interval": None,
        "interval_status": "not_estimated_for_natural_experiment" if design else "not_applicable",
        "assumption_ledger": {
            "support_assessment_id": row.get("assessment_id"),
            "mechanism": row.get("mechanism") if isinstance(row.get("mechanism"), dict) else {},
            "checked_caveats": [*assessment_caveats, *design_caveats],
            "negative_control_statuses": sorted(set(negative_statuses)),
            "untested": (
                "randomization",
                "controlled benchmark reproduction",
            ) if support_level == "natural_experiment" else (),
        },
    }
    if design:
        payload["boundary"] = {
            "boundary_id": design.get("boundary_id"),
            "boundary_type": design.get("boundary_type"),
            "boundary_at": design.get("boundary_at"),
            "project": design.get("project"),
            "stage_name": design.get("stage_name"),
            "non_randomized": True,
        }
        payload["identification_status"] = design.get("identification_status")
        payload["negative_control_status"] = design.get("negative_control_status")
        payload["sample_counts"] = {
            "treated_before_n": design.get("treated_before_n"),
            "treated_after_n": design.get("treated_after_n"),
            "control_before_n": design.get("control_before_n"),
            "control_after_n": design.get("control_after_n"),
        }
        payload["effect_estimate"] = {
            "treated_delta": design.get("treated_delta"),
            "control_delta": design.get("control_delta"),
            "difference_in_differences": design.get("difference_in_differences"),
            "placebo_delta": design.get("placebo_delta"),
            "balance": design.get("balance") if isinstance(design.get("balance"), dict) else {},
        }
        payload["negative_controls"] = [_negative_control_payload(control) for control in controls]
        payload["negative_control_sensitivity"] = _negative_control_sensitivity(
            design=design,
            controls=controls,
        )
    return payload


def _negative_control_payload(control: dict[str, Any]) -> dict[str, Any]:
    return {
        "control_id": control.get("control_id"),
        "control_kind": control.get("control_kind"),
        "support_required": control.get("support_required"),
        "status": control.get("status"),
        "primary_delta": control.get("primary_delta"),
        "control_delta": control.get("control_delta"),
        "placebo_delta": control.get("placebo_delta"),
        "interpretation": control.get("interpretation"),
        "support_consequence": control.get("support_consequence"),
    }


def _negative_control_sensitivity(
    *,
    design: dict[str, Any],
    controls: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = tuple(str(control.get("status") or "unknown") for control in controls)
    failed = sum(1 for status in statuses if status == "failed")
    passed = sum(1 for status in statuses if status == "passed")
    unavailable = sum(1 for status in statuses if status in {"unavailable", "unknown"})
    return {
        "design_status": design.get("negative_control_status"),
        "passed_count": passed,
        "failed_count": failed,
        "unavailable_count": unavailable,
        "support_ceiling": design.get("support_ceiling"),
        "interpretation": (
            "failed negative controls cap or refuse natural-experiment support"
            if failed
            else "negative controls did not contradict the matched design"
            if passed
            else "negative-control evidence unavailable"
        ),
    }


def _controlled_estimate_claims(payload: dict[str, Any] | None) -> tuple[MachineAttributionClaim, ...]:
    if not isinstance(payload, dict):
        return ()
    estimates = [row for row in payload.get("effect_estimates", []) if isinstance(row, dict)]
    packs = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for pack in packs:
        group = pack.get("run_group_id")
        if group:
            by_group.setdefault(str(group), []).append(pack)
    claims = []
    for estimate in estimates:
        group = str(estimate.get("run_group_id") or "")
        if not group:
            continue
        group_packs = by_group.get(group, [])
        first = group_packs[0] if group_packs else {}
        started = parse_datetime(str(first.get("started_at") or "")) if first else None
        metric = str(estimate.get("metric") or "duration_seconds")
        control = str(estimate.get("control_label") or "control")
        treatment = str(estimate.get("treatment_label") or "treatment")
        caveats = tuple(
            str(caveat)
            for row in group_packs
            for caveat in row.get("caveats", ())
            if caveat
        )
        fatal_caveats = _fatal_measurement_caveats(caveats)
        support_level: SupportLevel = "insufficient" if fatal_caveats else "controlled"
        claim_estimate = dict(estimate)
        if fatal_caveats:
            claim_estimate["refusal_reasons"] = [
                "controlled benchmark has fatal measurement caveats",
                *fatal_caveats,
            ]
        claims.append(MachineAttributionClaim(
            claim_type="machine_attribution",
            project=_project_from_pack(first),
            date=started.date() if started else None,
            metric=metric,
            effect_kind=f"controlled_benchmark:{group}",
            support_level=support_level,
            confidence=_controlled_confidence(estimate),
            summary=_controlled_summary(
                group=group,
                treatment=treatment,
                control=control,
                metric=metric,
                estimate=estimate,
            ),
            baseline={"label": control, "n": estimate.get("control_n"), "mean": estimate.get("control_mean")},
            comparison={"label": treatment, "n": estimate.get("treatment_n"), "mean": estimate.get("treatment_mean")},
            estimate=claim_estimate,
            source_ids=tuple(str(row.get("run_id")) for row in group_packs if row.get("run_id")),
            caveats=caveats,
        ))
    return tuple(claims)


def _fatal_measurement_caveats(caveats: tuple[str, ...]) -> list[str]:
    fatal_needles = (
        "observational manifest only",
        "no machine telemetry samples",
        "no complete timed phase",
        "internal-json has no complete timed phase",
        "internal-json capture has no parseable timestamps",
        "run exited nonzero",
    )
    return [
        caveat
        for caveat in caveats
        if any(needle in caveat for needle in fatal_needles)
    ]


def _claim_row(claim: MachineAttributionClaim) -> AnalysisClaimRow | None:
    try:
        return claim.to_analysis_claim()
    except ValueError:
        return None


def _claim_payload(row: AnalysisClaimRow) -> dict[str, Any]:
    return {
        "claim_id": row.claim_id,
        "claim_type": row.claim_type,
        "project": row.project,
        "date": row.date.isoformat() if row.date else None,
        "support_level": row.support_level,
        "confidence": row.confidence,
        "score": row.score,
        "summary": row.summary,
        "source_ids": list(row.source_ids),
        "relation_ids": list(row.relation_ids),
        "caveats": list(row.caveats),
        "payload": row.payload,
    }


def _optional_payload(path: Path | None, name: str) -> dict[str, Any] | None:
    payload = load_json_if_exists(path or resolve_analysis_path(name))
    return payload if isinstance(payload, dict) else None


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _confidence(value: object) -> float:
    return min(1.0, max(0.0, float(value) if isinstance(value, (int, float)) else 0.0))


def _controlled_confidence(estimate: dict[str, Any]) -> float:
    total = int(estimate.get("control_n") or 0) + int(estimate.get("treatment_n") or 0)
    return min(0.9, 0.55 + total * 0.025)


def _controlled_summary(
    *,
    group: str,
    treatment: str,
    control: str,
    metric: str,
    estimate: dict[str, Any],
) -> str:
    pieces = [
        f"Controlled benchmark {group}: {treatment} minus {control}",
        f"delta {estimate.get('delta')} {metric}",
    ]
    if estimate.get("ci_low") is not None and estimate.get("ci_high") is not None:
        pieces.append(f"95% CI [{estimate.get('ci_low')}, {estimate.get('ci_high')}]")
    if estimate.get("p_value") is not None:
        pieces.append(f"p={estimate.get('p_value')}")
    if estimate.get("p_value_method"):
        pieces.append(f"method={estimate.get('p_value_method')}")
    return "; ".join(pieces)


def _project_from_pack(pack: dict[str, Any]) -> str | None:
    for key in ("git_root", "cwd", "workload"):
        value = pack.get(key)
        if value:
            project = canonical_project_name(str(value))
            if project is not None:
                return project
    return None


def _estimate_score(estimate: dict[str, Any]) -> float:
    effect = estimate.get("effect_estimate")
    if isinstance(effect, dict):
        value = effect.get("difference_in_differences")
        if isinstance(value, (int, float)):
            return abs(float(value))
    for key in ("abs_delta", "delta", "effect_size", "difference_in_differences", "median_delta"):
        value = estimate.get(key)
        if isinstance(value, (int, float)):
            return abs(float(value))
    return 0.0


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


def _validate_confidence(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("MachineAttributionClaim.confidence must be between 0 and 1")


__all__ = [
    "MachineAttributionClaim",
    "MachineAttributionClaimAnalysis",
    "SupportLevel",
    "analyze_machine_attribution_claims",
    "write_machine_attribution_claims",
]
