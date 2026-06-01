"""Dry-run controlled benchmark planning from machine attribution candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_if_exists, load_json_object, resolve_analysis_path, save_json

from .causal_model import assess_causal_model
from .controlled_benchmarks import benchmark_readiness, benchmark_run_manifest
from .derivation_inventory import derivations_for_candidate, derivations_from_inventory


@dataclass(frozen=True)
class MachineBenchmarkPlan:
    plan_id: str
    candidate_id: str
    planning_status: str
    support_ceiling: str
    research_question: str
    hypothesis: str
    estimand: str
    primary_metric: str
    minimum_effect_of_interest: float | None
    repeats_per_cell: int
    blocking_keys: tuple[str, ...]
    design_variants: tuple[dict[str, Any], ...]
    causal_model_assessment: dict[str, Any]
    manifest_preview: dict[str, Any]
    run_manifest: tuple[dict[str, Any], ...]
    readiness: dict[str, Any]
    required_bindings: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineBenchmarkPlanAnalysis:
    generated_for: dict[str, Any]
    plan_count: int
    ready_plan_count: int
    plans: list[MachineBenchmarkPlan]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_benchmark_plans(
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    derivation_inventory_path: Path | None = None,
    derivations: tuple[dict[str, Any], ...] = (),
    repeats_per_cell: int = 3,
    limit: int = 10,
) -> MachineBenchmarkPlanAnalysis:
    payload = load_json_object(
        candidates_path or resolve_analysis_path("machine_attribution_candidates.json"),
        label="machine attribution candidates",
    )
    candidates = [row for row in payload.get("candidates", []) if isinstance(row, dict)]
    candidates.sort(key=lambda row: -float(row.get("priority_score") or 0.0))
    if limit > 0:
        candidates = candidates[:limit]
    inventory = (
        load_json_if_exists(derivation_inventory_path)
        if derivation_inventory_path is not None
        else load_json_if_exists(resolve_analysis_path("machine_derivation_inventory.json"))
        if candidates_path is None
        else None
    )
    inventory_payload = inventory if isinstance(inventory, dict) else None
    plans = [
        _plan_for_candidate(
            row,
            derivations=derivations or derivations_for_candidate(inventory_payload, row),
            repeats_per_cell=repeats_per_cell,
        )
        for row in candidates
    ]
    return MachineBenchmarkPlanAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_attribution_candidates.json",
            "limit": limit,
            "repeats_per_cell": repeats_per_cell,
            "derivation_count": len(derivations) or len(derivations_from_inventory(inventory_payload)),
            "derivation_source": "argument" if derivations else "machine_derivation_inventory.json",
        },
        plan_count=len(plans),
        ready_plan_count=sum(1 for plan in plans if plan.planning_status == "ready"),
        plans=plans,
        caveats=[
            "planning artifact only; no benchmark execution is performed",
            "ready plans still require operator review before manifest capture",
        ],
    )


def write_machine_benchmark_plans(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    derivation_inventory_path: Path | None = None,
    derivations: tuple[dict[str, Any], ...] = (),
    repeats_per_cell: int = 3,
    limit: int = 10,
) -> MachineBenchmarkPlanAnalysis:
    analysis = analyze_machine_benchmark_plans(
        start=start,
        end=end,
        candidates_path=candidates_path,
        derivation_inventory_path=derivation_inventory_path,
        derivations=derivations,
        repeats_per_cell=repeats_per_cell,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _plan_for_candidate(
    candidate: dict[str, Any],
    *,
    derivations: tuple[dict[str, Any], ...],
    repeats_per_cell: int,
) -> MachineBenchmarkPlan:
    candidate_id = str(candidate.get("candidate_id") or "candidate")
    metric = str(candidate.get("metric") or "duration_seconds")
    factor = str(candidate.get("suspected_factor") or "treatment")
    control_label = "baseline"
    treatment_label = _safe_label(factor)
    seed = _seed(candidate_id, metric, factor, repeats_per_cell)
    run_group_id = f"bench-{hashlib.sha1(candidate_id.encode()).hexdigest()[:12]}"
    randomized_order = _randomized_order(
        run_group_id=run_group_id,
        control_label=control_label,
        treatment_label=treatment_label,
        repeats_per_cell=repeats_per_cell,
        seed=seed,
    )
    causal_model = _causal_model(metric=metric, factor=factor)
    causal_assessment = assess_causal_model(causal_model, support_ceiling="controlled")
    manifest = {
        "controlled_benchmark": {
            "run_group_id": run_group_id,
            "derivations": list(derivations),
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": seed,
            "randomized_order": randomized_order,
            "control_label": control_label,
            "treatment_label": treatment_label,
            "internal_json": _internal_json_capture_contract(run_group_id),
            "telemetry": {"window_source": "manifest_timestamps"},
            "execution_hygiene_contract": _execution_hygiene_contract(),
            "metric": metric,
        },
        "pre_analysis": {
            "research_question": f"Does {factor} change {metric}?",
            "hypothesis": f"{factor} affects {metric}; direction must be confirmed before execution",
            "estimand": f"mean treatment-minus-control delta in {metric}",
            "estimator": "blocked bootstrap mean delta, stratified by cache_condition and derivation",
            "unit": "run",
            "primary_metric": metric,
            "secondary_metrics": ["internal-json phase durations", "telemetry overlap summaries"],
            "minimum_effect_of_interest": _minimum_effect(candidate),
            "inclusion_rules": ["fixed derivation set", "successful command exit", "telemetry window present"],
            "exclusion_rules": ["nonzero exit unless failure is the planned outcome", "missing internal-json phase log"],
            "stopping_rule": f"fixed {repeats_per_cell} repeats per treatment/cache cell; no interim looks",
            "blocking_keys": ["cache_condition", "derivation"],
            "negative_controls": ["unrelated stage duration if available", "pre-window telemetry"],
            "selected_design_variant": "blocked_randomization",
            "design_variants": _design_variants(has_derivations=bool(derivations), repeats_per_cell=repeats_per_cell),
            "execution_hygiene_contract": _execution_hygiene_contract(),
            "causal_model": causal_model,
            "causal_model_assessment": causal_assessment.to_dict(),
            "instrumentation_bundle": _instrumentation_bundle(),
            "power_note": _power_note(candidate, repeats_per_cell=repeats_per_cell),
            "support_ceiling": "controlled",
        },
        "candidate": {
            "candidate_id": candidate_id,
            "source_artifacts": candidate.get("source_artifacts") or [],
            "source_ids": candidate.get("source_ids") or [],
        },
    }
    readiness = benchmark_readiness(manifest)
    run_manifest = tuple(row.to_dict() for row in benchmark_run_manifest(manifest))
    required = []
    if not derivations:
        required.append("fixed_derivation_set")
    required.extend(issue for issue in readiness.issues if issue != "missing fixed derivation set")
    status = "ready" if readiness.controlled else "needs_binding"
    caveats = ["dry-run plan; not an executed manifest"]
    caveats.extend(f"readiness gap: {issue}" for issue in readiness.issues)
    return MachineBenchmarkPlan(
        plan_id=f"machine-benchmark-plan:{run_group_id}",
        candidate_id=candidate_id,
        planning_status=status,
        support_ceiling="controlled" if readiness.controlled else "candidate",
        research_question=manifest["pre_analysis"]["research_question"],
        hypothesis=manifest["pre_analysis"]["hypothesis"],
        estimand=manifest["pre_analysis"]["estimand"],
        primary_metric=metric,
        minimum_effect_of_interest=manifest["pre_analysis"]["minimum_effect_of_interest"],
        repeats_per_cell=repeats_per_cell,
        blocking_keys=tuple(manifest["pre_analysis"]["blocking_keys"]),
        design_variants=tuple(manifest["pre_analysis"]["design_variants"]),
        causal_model_assessment=causal_assessment.to_dict(),
        manifest_preview=manifest,
        run_manifest=run_manifest,
        readiness=readiness.to_dict(),
        required_bindings=tuple(required),
        caveats=tuple(caveats),
    )


def _randomized_order(
    *,
    run_group_id: str,
    control_label: str,
    treatment_label: str,
    repeats_per_cell: int,
    seed: int,
) -> list[dict[str, Any]]:
    rows = []
    for cache_condition in ("cold", "warm"):
        for label in (control_label, treatment_label):
            for repeat in range(max(repeats_per_cell, 1)):
                rows.append({
                    "run_id": f"{run_group_id}-{cache_condition}-{label}-{repeat + 1}",
                    "treatment_label": label,
                    "cache_condition": cache_condition,
                    "repeat": repeat + 1,
                })
    random.Random(seed).shuffle(rows)
    return rows


def _design_variants(*, has_derivations: bool, repeats_per_cell: int) -> list[dict[str, Any]]:
    fixed_workload_ceiling = "controlled" if has_derivations else "candidate"
    return [
        {
            "design_id": "blocked_randomization",
            "support_ceiling": fixed_workload_ceiling,
            "status": "ready" if has_derivations else "needs_fixed_derivation_set",
            "required_fields": ["fixed_derivation_set", "assignment_seed", "cache_condition", "randomized_order"],
            "blocking_keys": ["cache_condition", "derivation"],
            "estimator": "blocked bootstrap mean delta",
            "when_to_use": "default design for treatment/control benchmark comparisons",
            "limitations": [] if has_derivations else ["fixed derivation set is not bound"],
        },
        {
            "design_id": "paired_before_after",
            "support_ceiling": "natural_experiment_design",
            "status": "available_for_extant_boundaries",
            "required_fields": ["boundary_id", "matched_before_after_units", "parallel_trend_screen"],
            "blocking_keys": ["candidate_boundary", "project", "stage_or_command"],
            "estimator": "paired delta with boundary sensitivity analysis",
            "when_to_use": "existing boundary/change already separates before and after units",
            "limitations": ["non-randomized; cannot reach controlled support"],
        },
        {
            "design_id": "latin_square_ordering",
            "support_ceiling": "controlled" if has_derivations and repeats_per_cell >= 2 else "candidate",
            "status": "ready" if has_derivations and repeats_per_cell >= 2 else "needs_order_blocks",
            "required_fields": ["fixed_derivation_set", "order_block", "cache_condition", "treatment_label"],
            "blocking_keys": ["order_block", "cache_condition", "derivation"],
            "estimator": "order-block adjusted treatment contrast",
            "when_to_use": "order, thermal, or cache carryover is plausible",
            "limitations": [] if has_derivations and repeats_per_cell >= 2 else [
                "requires fixed derivations and at least two repeats per cell"
            ],
        },
        {
            "design_id": "factorial",
            "support_ceiling": "controlled" if has_derivations else "candidate",
            "status": "needs_factor_declaration",
            "required_fields": ["factor_names", "fixed_derivation_set", "randomized_order"],
            "blocking_keys": ["cache_condition", "derivation", "factor_level"],
            "estimator": "main effects plus predeclared interactions",
            "when_to_use": "multiple suspected factors must be separated in one campaign",
            "limitations": ["requires explicit factor aliases before fractional reduction"],
        },
        {
            "design_id": "fractional_factorial",
            "support_ceiling": "screening_only",
            "status": "disabled_until_aliases_declared",
            "required_fields": ["factor_names", "generator", "alias_table", "fixed_derivation_set"],
            "blocking_keys": ["cache_condition", "derivation", "fractional_block"],
            "estimator": "screening main effects; aliased effects refused as causal claims",
            "when_to_use": "factor space is too large for a full factorial campaign",
            "alias_policy": "every omitted interaction must be declared before execution",
            "limitations": ["cannot support unaliased interaction claims"],
        },
        {
            "design_id": "sequential",
            "support_ceiling": "controlled" if has_derivations else "candidate",
            "status": "disabled_until_interim_plan_declared",
            "required_fields": ["interim_looks", "alpha_spending_rule", "stopping_rule"],
            "blocking_keys": ["cache_condition", "derivation"],
            "estimator": "predeclared sequential estimate with alpha-spending correction",
            "when_to_use": "runs are expensive enough to justify predeclared interim looks",
            "interim_policy": "no peeking unless looks, alpha spending, and stopping rule are in the manifest",
            "limitations": ["disabled unless interim looks and stopping rule are preregistered"],
        },
    ]


def _execution_hygiene_contract() -> dict[str, Any]:
    return {
        "required_manifest_fields": [
            "host",
            "run_id",
            "run_group_id",
            "started_at",
            "ended_at",
            "monotonic_started_ns",
            "monotonic_ended_ns",
            "command",
            "exit_status",
            "execution_outcome",
            "measurement_context",
            "git",
            "pre_state",
            "post_state",
        ],
        "measurement_context_fields": [
            "host_boot_id",
            "system_generation",
            "kernel_release",
            "cpu_governor",
            "power_profile",
            "thermal_zone_policy",
            "env_digest",
        ],
        "execution_outcome_fields": [
            "status",
            "timeout_s",
            "censored",
            "retry_attempt",
            "warmup_discarded",
            "partial_output",
        ],
        "censoring_policy": "timeouts, cancellation, missing phase logs, and partial output must be explicit",
        "clock_policy": "record both wall-clock instants and monotonic nanoseconds; monotonic duration is primary",
        "warmup_policy": "warmup/discard decisions must be declared per run, never inferred after inspection",
    }


def _minimum_effect(candidate: dict[str, Any]) -> float | None:
    components = candidate.get("score_components")
    if not isinstance(components, dict):
        return None
    effect = components.get("effect_size")
    try:
        value = float(effect)
    except (TypeError, ValueError):
        return None
    return round(max(value * 0.25, 0.001), 6)


def _causal_model(*, metric: str, factor: str) -> dict[str, Any]:
    return {
        "treatment_variable": factor,
        "outcome_variable": metric,
        "blocking_variables": ["cache_condition", "derivation"],
        "adjustment_variables": ["host", "software_revision", "pre_window_pressure"],
        "forbidden_post_treatment_variables": ["during_run_phase_duration", "post_state"],
        "known_unobserved_confounders": ["thermal carryover", "operator background load outside captured telemetry"],
        "identification_note": (
            "controlled support requires fixed derivations, randomized order, cache-condition blocks, "
            "telemetry overlap, and internal-json provenance"
        ),
    }


def _instrumentation_bundle() -> dict[str, Any]:
    return {
        "name": "build_phase",
        "required": [
            "manifest timestamps",
            "command exit status",
            "fixed derivation drv_path",
            "Nix --log-format internal-json phase log",
            "machine telemetry overlap",
        ],
        "optional": ["perf stat counters", "service/cgroup state snapshot"],
        "expected_overhead": "low; heavy tracing is not enabled by this template",
    }


def _internal_json_capture_contract(run_group_id: str) -> dict[str, Any]:
    return {
        "capture_contract_version": 1,
        "path": f"<capture-root>/{run_group_id}/{{run_id}}/nix-internal-json.ndjson",
        "log_format": "internal-json",
        "capture_stream": "stderr",
        "argv_template": [
            "nix",
            "build",
            "--log-format",
            "internal-json",
            "{derivation_key}",
        ],
        "redirection": "capture the declared stream verbatim to path; do not postprocess before validation",
    }


def _power_note(candidate: dict[str, Any], *, repeats_per_cell: int) -> dict[str, Any]:
    return {
        "status": "approximate_pre_execution",
        "repeats_per_cell": max(repeats_per_cell, 1),
        "minimum_effect_of_interest": _minimum_effect(candidate),
        "interpretation": "if the post-run interval is wider than the minimum effect, refuse precision claims",
    }


def _safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")[:48] or "treatment"


def _seed(*parts: Any) -> int:
    return int(hashlib.sha1("\0".join(str(part) for part in parts).encode()).hexdigest()[:8], 16)


__all__ = [
    "MachineBenchmarkPlan",
    "MachineBenchmarkPlanAnalysis",
    "analyze_machine_benchmark_plans",
    "write_machine_benchmark_plans",
]
