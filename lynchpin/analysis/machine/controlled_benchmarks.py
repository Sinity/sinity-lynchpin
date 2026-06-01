"""Controlled benchmark manifest contract and estimators.

This module is infrastructure only: it validates and analyzes manifests that
future benchmark runners write, but it does not execute benchmarks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations, product
from math import comb
from pathlib import Path
import random
from statistics import mean
from typing import Any

from .causal_model import assess_causal_model
from .nix_internal_json import summarize_internal_json


@dataclass(frozen=True)
class BenchmarkManifestRun:
    run_id: str
    run_group_id: str
    sequence_index: int
    treatment_label: str
    cache_condition: str
    derivation_key: str | None
    internal_json_path: str | None
    telemetry_window_id: str
    telemetry_window_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkReadiness:
    controlled: bool
    issues: tuple[str, ...]
    run_group_id: str | None
    control_label: str | None
    treatment_label: str | None
    cache_conditions: tuple[str, ...]
    derivation_count: int
    randomized_run_count: int
    internal_json_path: str | None
    internal_json_log_format: str | None
    internal_json_capture_stream: str | None
    telemetry_window_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BootstrapEstimate:
    estimator: str
    metric: str
    control_label: str
    treatment_label: str
    control_n: int
    treatment_n: int
    control_mean: float
    treatment_mean: float
    delta: float
    ci_low: float
    ci_high: float
    confidence: float
    p_value: float | None
    p_value_method: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StratifiedBootstrapEstimate:
    estimator: str
    metric: str
    control_label: str
    treatment_label: str
    control_n: int
    treatment_n: int
    control_mean: float
    treatment_mean: float
    delta: float
    ci_low: float
    ci_high: float
    confidence: float
    p_value: float | None
    p_value_method: str | None
    stratum_count: int
    strata: tuple[str, ...]
    dropped_strata: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PairedBootstrapEstimate:
    estimator: str
    metric: str
    control_label: str
    treatment_label: str
    pair_n: int
    control_mean: float
    treatment_mean: float
    delta: float
    ci_low: float
    ci_high: float
    confidence: float
    p_value: float | None
    p_value_method: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkManifestValidation:
    valid: bool
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    schema: str | None
    run_id: str | None
    run_group_id: str | None
    started_at: str | None
    ended_at: str | None
    internal_json_path: str | None
    internal_json_summary: dict[str, Any] | None
    readiness: dict[str, Any] | None
    selected_run: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def benchmark_readiness(planned: dict[str, Any]) -> BenchmarkReadiness:
    """Validate the planned_treatment controlled-benchmark contract."""
    benchmark = _benchmark_block(planned)
    issues: list[str] = []

    run_group_id = _str_or_none(benchmark.get("run_group_id") or planned.get("run_group_id"))
    if run_group_id is None:
        issues.append("missing run_group_id")

    derivations = _list(benchmark.get("derivations") or planned.get("derivations"))
    if not derivations:
        issues.append("missing fixed derivation set")
    elif any(not _derivation_key(row) for row in derivations):
        issues.append("derivation set contains entries without drv_path/store_path/name")

    cache_conditions = tuple(
        str(item)
        for item in _list(benchmark.get("cache_conditions") or planned.get("cache_conditions"))
    )
    if not {"warm", "cold"}.issubset(set(cache_conditions)):
        issues.append("cache_conditions must include both warm and cold")

    randomization = _dict(benchmark.get("randomization") or planned.get("randomization"))
    randomized_order = _list(
        benchmark.get("randomized_order")
        or planned.get("randomized_order")
        or randomization.get("order")
    )
    assignment_seed = (
        benchmark.get("assignment_seed")
        or planned.get("assignment_seed")
        or randomization.get("assignment_seed")
        or randomization.get("seed")
    )
    if assignment_seed is None:
        issues.append("missing assignment_seed")
    if not randomized_order:
        issues.append("missing randomized run order")
    else:
        issues.extend(_randomized_order_issues(randomized_order))

    control_label = _str_or_none(
        benchmark.get("control_label")
        or planned.get("control_label")
        or _dict(planned.get("control")).get("label")
    )
    treatment_label = _str_or_none(
        benchmark.get("treatment_label")
        or planned.get("treatment_label")
        or planned.get("treatment")
        or _dict(planned.get("treatment")).get("label")
    )
    if control_label is None:
        issues.append("missing control_label")
    if treatment_label is None:
        issues.append("missing treatment_label")
    if control_label is not None and treatment_label is not None and randomized_order:
        issues.extend(_assignment_balance_issues(
            randomized_order,
            control_label=control_label,
            treatment_label=treatment_label,
        ))

    internal_json = _dict(benchmark.get("internal_json") or planned.get("internal_json"))
    internal_json_path = _str_or_none(internal_json.get("path") or planned.get("internal_json_path"))
    if internal_json_path is None:
        issues.append("missing nix internal-json capture path")
    internal_json_log_format = _str_or_none(internal_json.get("log_format"))
    if internal_json_log_format != "internal-json":
        issues.append("internal_json.log_format must be internal-json")
    internal_json_capture_stream = _str_or_none(internal_json.get("capture_stream"))
    if internal_json_capture_stream not in {"stderr", "stdout"}:
        issues.append("internal_json.capture_stream must be stderr or stdout")
    argv_template = internal_json.get("argv_template")
    if not isinstance(argv_template, list) or not all(isinstance(item, str) for item in argv_template):
        issues.append("internal_json.argv_template must be a list of strings")
    elif "--log-format" not in argv_template or "internal-json" not in argv_template:
        issues.append("internal_json.argv_template must request --log-format internal-json")

    telemetry = _dict(benchmark.get("telemetry") or planned.get("telemetry"))
    telemetry_window_source = _str_or_none(telemetry.get("window_source"))
    if telemetry_window_source is None:
        issues.append("missing telemetry window linkage")

    pre_analysis = _dict(planned.get("pre_analysis") or benchmark.get("pre_analysis"))
    if not pre_analysis:
        issues.append("missing pre_analysis record")
    else:
        issues.extend(_pre_analysis_issues(pre_analysis))

    controlled = not issues
    return BenchmarkReadiness(
        controlled=controlled,
        issues=tuple(issues),
        run_group_id=run_group_id,
        control_label=control_label,
        treatment_label=treatment_label,
        cache_conditions=cache_conditions,
        derivation_count=len(derivations),
        randomized_run_count=len(randomized_order),
        internal_json_path=internal_json_path,
        internal_json_log_format=internal_json_log_format,
        internal_json_capture_stream=internal_json_capture_stream,
        telemetry_window_source=telemetry_window_source,
    )


def is_controlled_benchmark_manifest(planned: dict[str, Any]) -> bool:
    return benchmark_readiness(planned).controlled


def is_template_benchmark_manifest(payload: dict[str, Any]) -> bool:
    return (
        payload.get("schema") == "lynchpin.machine_experiment.template.v1"
        or _str_or_none(payload.get("template_status")) is not None
    )


def benchmark_run_manifest(planned: dict[str, Any]) -> tuple[BenchmarkManifestRun, ...]:
    """Expand a controlled benchmark plan into deterministic per-run rows."""
    benchmark = _benchmark_block(planned)
    readiness = benchmark_readiness(planned)
    run_group_id = readiness.run_group_id or "missing-run-group"
    derivations = _list(benchmark.get("derivations") or planned.get("derivations"))
    derivation_keys = tuple(_derivation_key(row) for row in derivations if _derivation_key(row))
    randomized_order = _list(benchmark.get("randomized_order") or planned.get("randomized_order"))
    internal_json = _dict(benchmark.get("internal_json") or planned.get("internal_json"))
    telemetry_source = readiness.telemetry_window_source
    rows = []
    for idx, row in enumerate(randomized_order):
        if not isinstance(row, dict):
            continue
        run_id = _str_or_none(row.get("run_id")) or f"{run_group_id}-{idx + 1:03d}"
        derivation_key = (
            _str_or_none(row.get("derivation") or row.get("derivation_key") or row.get("drv_path") or row.get("store_path"))
            or (derivation_keys[idx % len(derivation_keys)] if derivation_keys else None)
        )
        rows.append(
            BenchmarkManifestRun(
                run_id=run_id,
                run_group_id=run_group_id,
                sequence_index=idx + 1,
                treatment_label=str(row.get("treatment_label") or row.get("treatment") or ""),
                cache_condition=str(row.get("cache_condition") or ""),
                derivation_key=derivation_key,
                internal_json_path=_internal_json_path(internal_json, run_group_id=run_group_id, run_id=run_id),
                telemetry_window_id=f"{run_group_id}:{run_id}:manifest_timestamps",
                telemetry_window_source=telemetry_source,
            )
        )
    return tuple(rows)


def validate_executed_benchmark_manifest(
    payload: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    require_file_refs: bool = False,
) -> BenchmarkManifestValidation:
    """Validate a completed benchmark manifest without executing anything."""
    issues: list[str] = []
    warnings: list[str] = []
    schema = _str_or_none(payload.get("schema"))
    run_id = _str_or_none(payload.get("run_id"))
    run_group_id = _str_or_none(payload.get("run_group_id"))
    planned = _dict(payload.get("planned_treatment"))
    readiness = benchmark_readiness(planned).to_dict() if planned else None
    selected_run = _selected_run(planned)

    if is_template_benchmark_manifest(payload):
        issues.append("template manifest is not an executed run")
    if run_id is None:
        issues.append("missing run_id")
    if run_group_id is None and planned:
        warnings.append("missing run_group_id; controlled grouping must be inferred from planned_treatment")
    issues.extend(selected_run_assignment_issues(planned, payload_run_id=run_id, payload_run_group_id=run_group_id))
    if _str_or_none(payload.get("host")) is None:
        issues.append("missing host")
    if _str_or_none(payload.get("workload")) is None:
        issues.append("missing workload")
    command = payload.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        issues.append("command must be a list of strings")

    started = _parse_instant(payload.get("started_at"))
    ended = _parse_instant(payload.get("ended_at"))
    if started is None:
        issues.append("missing or invalid started_at")
    if payload.get("ended_at") is not None and ended is None:
        issues.append("invalid ended_at")
    if started is not None and ended is not None and ended < started:
        issues.append("ended_at precedes started_at")
    mono_started = payload.get("monotonic_started_ns")
    mono_ended = payload.get("monotonic_ended_ns")
    if not isinstance(mono_started, int):
        issues.append("missing or invalid monotonic_started_ns")
    if not isinstance(mono_ended, int):
        issues.append("missing or invalid monotonic_ended_ns")
    if isinstance(mono_started, int) and isinstance(mono_ended, int) and mono_ended < mono_started:
        issues.append("monotonic_ended_ns precedes monotonic_started_ns")
    if payload.get("exit_status") is not None and not isinstance(payload.get("exit_status"), int):
        issues.append("exit_status must be an integer or null")
    issues.extend(_execution_outcome_issues(_dict(payload.get("execution_outcome"))))
    issues.extend(_measurement_context_issues(_dict(payload.get("measurement_context"))))

    git = _dict(payload.get("git"))
    for key in ("root", "head", "branch", "dirty"):
        if key not in git:
            warnings.append(f"git.{key} missing")
    if "dirty" in git and not isinstance(git.get("dirty"), bool):
        issues.append("git.dirty must be boolean")
    if not isinstance(payload.get("pre_state"), dict):
        issues.append("pre_state must be an object")
    if not isinstance(payload.get("post_state"), dict):
        issues.append("post_state must be an object")

    internal_json_path = _manifest_internal_json_path(payload, planned)
    internal_json_summary = None
    if planned and readiness is not None and not readiness.get("controlled"):
        for issue in readiness.get("issues", ()):
            warnings.append(f"planned_treatment not controlled-ready: {issue}")
    issues.extend(_internal_json_path_consistency_issues(payload, planned))
    if internal_json_path is None:
        warnings.append("missing internal-json path")
    elif _is_templated_path(internal_json_path):
        issues.append("internal-json path is still templated")
    else:
        internal_json_ref = _resolve_manifest_ref(internal_json_path, manifest_path)
        if require_file_refs and not internal_json_ref.exists():
            issues.append(f"internal-json path does not exist: {internal_json_path}")
        if internal_json_ref.exists():
            summary = summarize_internal_json(internal_json_ref).to_dict()
            internal_json_summary = summary
            if int(summary.get("parsed_count") or 0) == 0:
                issues.append("internal-json capture has no parsed rows")
            if int(summary.get("phase_count") or 0) == 0:
                issues.append("internal-json capture has no reconstructable phases")
            elif not any(
                isinstance(phase, dict) and phase.get("status") == "complete"
                for phase in summary.get("phases", ())
            ):
                warnings.append("internal-json capture has no complete timed phases")
            for caveat in summary.get("caveats", ()):
                warnings.append(f"internal-json: {caveat}")

    return BenchmarkManifestValidation(
        valid=not issues,
        issues=tuple(issues),
        warnings=tuple(warnings),
        schema=schema,
        run_id=run_id,
        run_group_id=run_group_id,
        started_at=started.isoformat() if started else None,
        ended_at=ended.isoformat() if ended else None,
        internal_json_path=internal_json_path,
        internal_json_summary=internal_json_summary,
        readiness=readiness,
        selected_run=selected_run,
    )


def selected_run_assignment_issues(
    planned: dict[str, Any],
    *,
    payload_run_id: str | None = None,
    payload_run_group_id: str | None = None,
) -> tuple[str, ...]:
    """Return evidence-chain gaps between an executed run and its assignment.

    A controlled benchmark claim is only meaningful if the executed manifest
    names one scheduled randomized assignment. The plan-level readiness check
    proves the group design; this function proves the per-run binding.
    """
    if not planned:
        return ()
    benchmark = _benchmark_block(planned)
    readiness = benchmark_readiness(planned)
    if not readiness.controlled:
        return ()

    issues: list[str] = []
    selected = _selected_run(planned)
    if selected is None:
        return ("planned_treatment.selected_run missing",)

    selected_run_id = _str_or_none(selected.get("run_id"))
    if selected_run_id is None:
        issues.append("planned_treatment.selected_run.run_id missing")
    elif payload_run_id is not None and selected_run_id != payload_run_id:
        issues.append(
            f"planned_treatment.selected_run.run_id {selected_run_id!r} does not match executed run_id {payload_run_id!r}"
        )

    run_group_id = readiness.run_group_id
    if payload_run_group_id is not None and run_group_id is not None and payload_run_group_id != run_group_id:
        issues.append(
            f"executed run_group_id {payload_run_group_id!r} does not match planned run_group_id {run_group_id!r}"
        )

    randomized_order = _list(
        benchmark.get("randomized_order")
        or planned.get("randomized_order")
        or _dict(benchmark.get("randomization") or planned.get("randomization")).get("order")
    )
    scheduled = _scheduled_run(randomized_order, selected_run_id)
    if selected_run_id is not None and scheduled is None:
        issues.append(f"planned_treatment.selected_run.run_id {selected_run_id!r} is not in randomized_order")
    if scheduled is None:
        return tuple(issues)

    for key, scheduled_key in (
        ("treatment_label", "treatment_label"),
        ("cache_condition", "cache_condition"),
    ):
        selected_value = _str_or_none(selected.get(key))
        scheduled_value = _str_or_none(scheduled.get(scheduled_key))
        if key == "treatment_label":
            scheduled_value = scheduled_value or _str_or_none(scheduled.get("treatment"))
        if selected_value is None:
            issues.append(f"planned_treatment.selected_run.{key} missing")
        elif scheduled_value is not None and selected_value != scheduled_value:
            issues.append(
                f"planned_treatment.selected_run.{key} {selected_value!r} does not match randomized_order {scheduled_value!r}"
            )

    selected_sequence = selected.get("sequence_index")
    scheduled_index = randomized_order.index(scheduled) + 1
    if not isinstance(selected_sequence, int):
        issues.append("planned_treatment.selected_run.sequence_index missing")
    elif selected_sequence != scheduled_index:
        issues.append(
            f"planned_treatment.selected_run.sequence_index {selected_sequence} does not match randomized_order index {scheduled_index}"
        )

    selected_derivation = _str_or_none(selected.get("derivation_key"))
    scheduled_derivation = _str_or_none(
        scheduled.get("derivation_key") or scheduled.get("derivation") or scheduled.get("drv_path") or scheduled.get("store_path")
    )
    if selected_derivation is None:
        issues.append("planned_treatment.selected_run.derivation_key missing")
    elif scheduled_derivation is not None and selected_derivation != scheduled_derivation:
        issues.append(
            f"planned_treatment.selected_run.derivation_key {selected_derivation!r} does not match randomized_order {scheduled_derivation!r}"
        )

    expected_telemetry = f"{run_group_id}:{selected_run_id}:manifest_timestamps" if run_group_id and selected_run_id else None
    selected_telemetry = _str_or_none(selected.get("telemetry_window_id"))
    if selected_telemetry is None:
        issues.append("planned_treatment.selected_run.telemetry_window_id missing")
    elif expected_telemetry is not None and selected_telemetry != expected_telemetry:
        issues.append(
            f"planned_treatment.selected_run.telemetry_window_id {selected_telemetry!r} does not match expected {expected_telemetry!r}"
        )

    if _str_or_none(selected.get("internal_json_path")) is None:
        issues.append("planned_treatment.selected_run.internal_json_path missing")

    return tuple(issues)


def bootstrap_delta_ci(
    control: tuple[float, ...],
    treatment: tuple[float, ...],
    *,
    metric: str,
    control_label: str,
    treatment_label: str,
    confidence: float = 0.95,
    iterations: int = 1000,
    seed: int = 0,
) -> BootstrapEstimate | None:
    """Return a deterministic bootstrap CI for treatment minus control."""
    if not control or not treatment:
        return None
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(max(iterations, 1)):
        c = [control[rng.randrange(len(control))] for _ in control]
        t = [treatment[rng.randrange(len(treatment))] for _ in treatment]
        deltas.append(mean(t) - mean(c))
    deltas.sort()
    alpha = max(0.0, min(1.0, 1.0 - confidence))
    low_idx = min(len(deltas) - 1, max(0, int((alpha / 2.0) * len(deltas))))
    high_idx = min(len(deltas) - 1, max(0, int((1.0 - alpha / 2.0) * len(deltas)) - 1))
    p_value, p_value_method = permutation_delta_p_value(control, treatment, seed=seed)
    return BootstrapEstimate(
        estimator="unpaired_bootstrap_mean_delta",
        metric=metric,
        control_label=control_label,
        treatment_label=treatment_label,
        control_n=len(control),
        treatment_n=len(treatment),
        control_mean=round(mean(control), 6),
        treatment_mean=round(mean(treatment), 6),
        delta=round(mean(treatment) - mean(control), 6),
        ci_low=round(deltas[low_idx], 6),
        ci_high=round(deltas[high_idx], 6),
        confidence=confidence,
        p_value=p_value,
        p_value_method=p_value_method,
    )


def paired_bootstrap_delta_ci(
    pairs: tuple[tuple[float, float], ...],
    *,
    metric: str,
    control_label: str,
    treatment_label: str,
    confidence: float = 0.95,
    iterations: int = 1000,
    seed: int = 0,
) -> PairedBootstrapEstimate | None:
    """Return a deterministic paired bootstrap CI for treatment minus control."""
    if not pairs:
        return None
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(max(iterations, 1)):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        deltas.append(mean(treatment - control for control, treatment in sample))
    deltas.sort()
    alpha = max(0.0, min(1.0, 1.0 - confidence))
    low_idx = min(len(deltas) - 1, max(0, int((alpha / 2.0) * len(deltas))))
    high_idx = min(len(deltas) - 1, max(0, int((1.0 - alpha / 2.0) * len(deltas)) - 1))
    control_values = tuple(control for control, _ in pairs)
    treatment_values = tuple(treatment for _, treatment in pairs)
    p_value, p_value_method = paired_sign_flip_p_value(pairs)
    return PairedBootstrapEstimate(
        estimator="paired_bootstrap_mean_delta",
        metric=metric,
        control_label=control_label,
        treatment_label=treatment_label,
        pair_n=len(pairs),
        control_mean=round(mean(control_values), 6),
        treatment_mean=round(mean(treatment_values), 6),
        delta=round(mean(treatment - control for control, treatment in pairs), 6),
        ci_low=round(deltas[low_idx], 6),
        ci_high=round(deltas[high_idx], 6),
        confidence=confidence,
        p_value=p_value,
        p_value_method=p_value_method,
    )


def stratified_bootstrap_delta_ci(
    control_by_stratum: dict[str, tuple[float, ...]],
    treatment_by_stratum: dict[str, tuple[float, ...]],
    *,
    metric: str,
    control_label: str,
    treatment_label: str,
    confidence: float = 0.95,
    iterations: int = 1000,
    seed: int = 0,
) -> StratifiedBootstrapEstimate | None:
    """Return a deterministic blocked bootstrap over complete strata.

    The estimand is the weighted mean of within-stratum treatment-control
    deltas. Strata are weighted by their complete-pair capacity
    ``min(control_n, treatment_n)`` so replicated derivation/cache blocks carry
    proportionate evidence while incomplete strata are explicit metadata, not
    silent dilution.
    """
    complete = tuple(
        stratum
        for stratum in sorted(set(control_by_stratum) | set(treatment_by_stratum))
        if control_by_stratum.get(stratum) and treatment_by_stratum.get(stratum)
    )
    if not complete:
        return None
    dropped = tuple(
        stratum
        for stratum in sorted(set(control_by_stratum) | set(treatment_by_stratum))
        if stratum not in complete
    )
    weights = {
        stratum: min(len(control_by_stratum[stratum]), len(treatment_by_stratum[stratum]))
        for stratum in complete
    }

    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(max(iterations, 1)):
        sampled_strata = [complete[rng.randrange(len(complete))] for _ in complete]
        weighted_delta = 0.0
        total_weight = 0
        for stratum in sampled_strata:
            control = control_by_stratum[stratum]
            treatment = treatment_by_stratum[stratum]
            c = [control[rng.randrange(len(control))] for _ in control]
            t = [treatment[rng.randrange(len(treatment))] for _ in treatment]
            weight = weights[stratum]
            weighted_delta += weight * (mean(t) - mean(c))
            total_weight += weight
        deltas.append(weighted_delta / total_weight)
    deltas.sort()

    control_mean = _weighted_stratum_mean(control_by_stratum, complete, weights)
    treatment_mean = _weighted_stratum_mean(treatment_by_stratum, complete, weights)
    p_value, p_value_method = stratified_permutation_delta_p_value(
        {key: control_by_stratum[key] for key in complete},
        {key: treatment_by_stratum[key] for key in complete},
        seed=seed,
    )
    alpha = max(0.0, min(1.0, 1.0 - confidence))
    low_idx = min(len(deltas) - 1, max(0, int((alpha / 2.0) * len(deltas))))
    high_idx = min(len(deltas) - 1, max(0, int((1.0 - alpha / 2.0) * len(deltas)) - 1))
    return StratifiedBootstrapEstimate(
        estimator="stratified_bootstrap_mean_delta",
        metric=metric,
        control_label=control_label,
        treatment_label=treatment_label,
        control_n=sum(len(control_by_stratum[stratum]) for stratum in complete),
        treatment_n=sum(len(treatment_by_stratum[stratum]) for stratum in complete),
        control_mean=round(control_mean, 6),
        treatment_mean=round(treatment_mean, 6),
        delta=round(treatment_mean - control_mean, 6),
        ci_low=round(deltas[low_idx], 6),
        ci_high=round(deltas[high_idx], 6),
        confidence=confidence,
        p_value=p_value,
        p_value_method=p_value_method,
        stratum_count=len(complete),
        strata=complete,
        dropped_strata=dropped,
    )


def _weighted_stratum_mean(
    values_by_stratum: dict[str, tuple[float, ...]],
    strata: tuple[str, ...],
    weights: dict[str, int],
) -> float:
    weighted = sum(weights[stratum] * mean(values_by_stratum[stratum]) for stratum in strata)
    return weighted / sum(weights[stratum] for stratum in strata)


def permutation_delta_p_value(
    control: tuple[float, ...],
    treatment: tuple[float, ...],
    *,
    seed: int = 0,
    max_exact_partitions: int = 20_000,
    iterations: int = 10_000,
) -> tuple[float | None, str | None]:
    """Two-sided randomization p-value for exchangeable treatment labels."""
    if not control or not treatment:
        return None, None
    pooled = tuple((*control, *treatment))
    control_n = len(control)
    observed = abs(mean(treatment) - mean(control))
    partition_count = comb(len(pooled), control_n)
    if partition_count <= max_exact_partitions:
        extreme = 0
        for control_idx in combinations(range(len(pooled)), control_n):
            control_set = set(control_idx)
            c = [pooled[idx] for idx in control_idx]
            t = [value for idx, value in enumerate(pooled) if idx not in control_set]
            if abs(mean(t) - mean(c)) >= observed - 1e-12:
                extreme += 1
        return round(extreme / partition_count, 6), "exact_label_permutation_two_sided"

    rng = random.Random(seed)
    extreme = 0
    draws = max(iterations, 1)
    indices = list(range(len(pooled)))
    for _ in range(draws):
        rng.shuffle(indices)
        control_idx = set(indices[:control_n])
        c = [value for idx, value in enumerate(pooled) if idx in control_idx]
        t = [value for idx, value in enumerate(pooled) if idx not in control_idx]
        if abs(mean(t) - mean(c)) >= observed - 1e-12:
            extreme += 1
    return round((extreme + 1) / (draws + 1), 6), "monte_carlo_label_permutation_two_sided"


def paired_sign_flip_p_value(
    pairs: tuple[tuple[float, float], ...],
    *,
    seed: int = 0,
    max_exact_pairs: int = 16,
    iterations: int = 10_000,
) -> tuple[float | None, str | None]:
    """Exact two-sided paired randomization p-value via sign flips."""
    if not pairs:
        return None, None
    deltas = tuple(treatment - control for control, treatment in pairs)
    observed = abs(mean(deltas))
    if len(deltas) > max_exact_pairs:
        rng = random.Random(seed)
        extreme = 0
        draws = max(iterations, 1)
        for _ in range(draws):
            candidate = abs(mean((1.0 if rng.randrange(2) else -1.0) * delta for delta in deltas))
            if candidate >= observed - 1e-12:
                extreme += 1
        return round((extreme + 1) / (draws + 1), 6), "monte_carlo_paired_sign_flip_two_sided"

    extreme = 0
    total = 2 ** len(deltas)
    for signs in product((-1.0, 1.0), repeat=len(deltas)):
        candidate = abs(mean(sign * delta for sign, delta in zip(signs, deltas, strict=True)))
        if candidate >= observed - 1e-12:
            extreme += 1
    return round(extreme / total, 6), "exact_paired_sign_flip_two_sided"


def stratified_permutation_delta_p_value(
    control_by_stratum: dict[str, tuple[float, ...]],
    treatment_by_stratum: dict[str, tuple[float, ...]],
    *,
    seed: int = 0,
    max_exact_partitions: int = 20_000,
    iterations: int = 10_000,
) -> tuple[float | None, str | None]:
    """Two-sided randomization p-value preserving complete strata."""
    strata = tuple(
        stratum
        for stratum in sorted(set(control_by_stratum) | set(treatment_by_stratum))
        if control_by_stratum.get(stratum) and treatment_by_stratum.get(stratum)
    )
    if not strata:
        return None, None
    weights = {stratum: min(len(control_by_stratum[stratum]), len(treatment_by_stratum[stratum])) for stratum in strata}
    observed = abs(
        _weighted_stratum_mean(treatment_by_stratum, strata, weights)
        - _weighted_stratum_mean(control_by_stratum, strata, weights)
    )
    partition_count = 1
    partitions_by_stratum: dict[str, list[tuple[int, ...]]] = {}
    for stratum in strata:
        n = len(control_by_stratum[stratum]) + len(treatment_by_stratum[stratum])
        control_n = len(control_by_stratum[stratum])
        partition_count *= comb(n, control_n)
        if partition_count <= max_exact_partitions:
            partitions_by_stratum[stratum] = list(combinations(range(n), control_n))

    if partition_count <= max_exact_partitions:
        extreme = 0
        partition_sets = [partitions_by_stratum[stratum] for stratum in strata]
        for assignments in product(*partition_sets):
            delta = _stratified_assignment_delta(
                control_by_stratum,
                treatment_by_stratum,
                strata,
                weights,
                assignments,
            )
            if abs(delta) >= observed - 1e-12:
                extreme += 1
        return round(extreme / partition_count, 6), "exact_stratified_label_permutation_two_sided"

    rng = random.Random(seed)
    extreme = 0
    draws = max(iterations, 1)
    for _ in range(draws):
        assignments = []
        for stratum in strata:
            n = len(control_by_stratum[stratum]) + len(treatment_by_stratum[stratum])
            indices = list(range(n))
            rng.shuffle(indices)
            assignments.append(tuple(indices[:len(control_by_stratum[stratum])]))
        delta = _stratified_assignment_delta(
            control_by_stratum,
            treatment_by_stratum,
            strata,
            weights,
            tuple(assignments),
        )
        if abs(delta) >= observed - 1e-12:
            extreme += 1
    return round((extreme + 1) / (draws + 1), 6), "monte_carlo_stratified_label_permutation_two_sided"


def _stratified_assignment_delta(
    control_by_stratum: dict[str, tuple[float, ...]],
    treatment_by_stratum: dict[str, tuple[float, ...]],
    strata: tuple[str, ...],
    weights: dict[str, int],
    assignments: tuple[tuple[int, ...], ...],
) -> float:
    weighted = 0.0
    total_weight = 0
    for stratum, control_idx in zip(strata, assignments, strict=True):
        pooled = tuple((*control_by_stratum[stratum], *treatment_by_stratum[stratum]))
        control_set = set(control_idx)
        c = [pooled[idx] for idx in control_idx]
        t = [value for idx, value in enumerate(pooled) if idx not in control_set]
        weight = weights[stratum]
        weighted += weight * (mean(t) - mean(c))
        total_weight += weight
    return weighted / total_weight


def _benchmark_block(planned: dict[str, Any]) -> dict[str, Any]:
    block = _dict(planned.get("controlled_benchmark") or planned.get("benchmark"))
    return block if block else planned


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_instant(value: object) -> datetime | None:
    text = _str_or_none(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _manifest_internal_json_path(payload: dict[str, Any], planned: dict[str, Any]) -> str | None:
    selected = _dict(planned.get("selected_run"))
    value = _str_or_none(selected.get("internal_json_path"))
    if value is not None:
        return value
    benchmark = _benchmark_block(planned)
    internal_json = _dict(benchmark.get("internal_json") or planned.get("internal_json"))
    value = _str_or_none(internal_json.get("path") or payload.get("internal_json_path"))
    if value is not None:
        return value
    return _str_or_none(payload.get("nix_internal_json_path"))


def _internal_json_path_consistency_issues(payload: dict[str, Any], planned: dict[str, Any]) -> tuple[str, ...]:
    paths = _declared_internal_json_paths(payload, planned)
    issues: list[str] = []
    for label, value in paths:
        if _is_templated_path(value):
            issues.append(f"{label} is still templated")
    concrete = {value for _, value in paths if not _is_templated_path(value)}
    if len(concrete) > 1:
        labels = ", ".join(label for label, _ in paths)
        issues.append(f"internal-json path declarations disagree across {labels}")
    return tuple(issues)


def _declared_internal_json_paths(payload: dict[str, Any], planned: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    benchmark = _benchmark_block(planned)
    internal_json = _dict(benchmark.get("internal_json") or planned.get("internal_json"))
    selected = _dict(planned.get("selected_run"))
    fields = (
        ("planned_treatment.selected_run.internal_json_path", selected.get("internal_json_path")),
        ("planned_treatment.controlled_benchmark.internal_json.path", internal_json.get("path")),
        ("internal_json_path", payload.get("internal_json_path")),
        ("nix_internal_json_path", payload.get("nix_internal_json_path")),
    )
    return tuple((label, value) for label, raw in fields if (value := _str_or_none(raw)) is not None)


def _is_templated_path(value: str) -> bool:
    return "{" in value or "}" in value or value.startswith("<")


def _selected_run(planned: dict[str, Any]) -> dict[str, Any] | None:
    selected = _dict(planned.get("selected_run"))
    return selected or None


def _scheduled_run(rows: list[Any], run_id: str | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    for row in rows:
        if isinstance(row, dict) and _str_or_none(row.get("run_id")) == run_id:
            return row
    return None


def _resolve_manifest_ref(value: str, manifest_path: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute() or manifest_path is None:
        return path
    return manifest_path.parent / path


def _derivation_key(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    return _str_or_none(row.get("drv_path") or row.get("store_path") or row.get("name"))


def _randomized_order_issues(rows: list[Any]) -> list[str]:
    issues = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"randomized run {idx + 1} is not an object")
            continue
        if _str_or_none(row.get("run_id")) is None:
            issues.append(f"randomized run {idx + 1} missing run_id")
        if _str_or_none(row.get("treatment_label") or row.get("treatment")) is None:
            issues.append(f"randomized run {idx + 1} missing treatment_label")
        if _str_or_none(row.get("cache_condition")) is None:
            issues.append(f"randomized run {idx + 1} missing cache_condition")
    return issues


def _assignment_balance_issues(
    rows: list[Any],
    *,
    control_label: str,
    treatment_label: str,
) -> list[str]:
    by_cache: dict[str, set[str]] = {}
    observed_labels = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _str_or_none(row.get("treatment_label") or row.get("treatment"))
        cache = _str_or_none(row.get("cache_condition"))
        if label is None or cache is None:
            continue
        observed_labels.add(label)
        by_cache.setdefault(cache, set()).add(label)
    issues = []
    missing = {control_label, treatment_label} - observed_labels
    if missing:
        issues.append(f"randomized order missing treatment labels: {', '.join(sorted(missing))}")
    for cache, labels in sorted(by_cache.items()):
        if not {control_label, treatment_label}.issubset(labels):
            issues.append(f"cache condition {cache} lacks both control and treatment assignments")
    return issues


def _pre_analysis_issues(pre_analysis: dict[str, Any]) -> list[str]:
    issues = []
    required = (
        "research_question",
        "hypothesis",
        "estimand",
        "unit",
        "primary_metric",
        "inclusion_rules",
        "exclusion_rules",
        "blocking_keys",
        "support_ceiling",
    )
    for key in required:
        value = pre_analysis.get(key)
        if value is None or value == "" or value == []:
            issues.append(f"pre_analysis missing {key}")
    causal_model = pre_analysis.get("causal_model")
    if not isinstance(causal_model, dict):
        issues.append("pre_analysis missing causal_model")
    else:
        assessment = assess_causal_model(
            causal_model,
            support_ceiling=str(pre_analysis.get("support_ceiling") or "unknown"),
        )
        issues.extend(assessment.issues)
    if not isinstance(pre_analysis.get("instrumentation_bundle"), dict):
        issues.append("pre_analysis missing instrumentation_bundle")
    if not isinstance(pre_analysis.get("power_note"), dict):
        issues.append("pre_analysis missing power_note")
    variants = pre_analysis.get("design_variants")
    if variants is not None:
        if not isinstance(variants, list) or not all(isinstance(row, dict) for row in variants):
            issues.append("pre_analysis design_variants must be a list of objects")
        elif not any(row.get("design_id") == pre_analysis.get("selected_design_variant") for row in variants):
            issues.append("pre_analysis selected_design_variant is not in design_variants")
    hygiene = pre_analysis.get("execution_hygiene_contract")
    if hygiene is not None and not isinstance(hygiene, dict):
        issues.append("pre_analysis execution_hygiene_contract must be an object")
    return issues


def _execution_outcome_issues(outcome: dict[str, Any]) -> list[str]:
    if not outcome:
        return ["missing execution_outcome"]
    issues = []
    status = _str_or_none(outcome.get("status"))
    if status is None:
        issues.append("execution_outcome.status missing")
    elif status not in {"success", "failure", "timeout", "cancelled"}:
        issues.append("execution_outcome.status must be success/failure/timeout/cancelled")
    if not isinstance(outcome.get("censored"), bool):
        issues.append("execution_outcome.censored must be boolean")
    retry = outcome.get("retry_attempt")
    if not isinstance(retry, int) or retry < 1:
        issues.append("execution_outcome.retry_attempt must be a positive integer")
    if not isinstance(outcome.get("warmup_discarded"), bool):
        issues.append("execution_outcome.warmup_discarded must be boolean")
    if not isinstance(outcome.get("partial_output"), bool):
        issues.append("execution_outcome.partial_output must be boolean")
    timeout = outcome.get("timeout_s")
    if timeout is not None and not isinstance(timeout, int | float):
        issues.append("execution_outcome.timeout_s must be numeric or null")
    if status == "timeout" and not outcome.get("censored"):
        issues.append("timeout executions must be marked censored")
    return issues


def _measurement_context_issues(context: dict[str, Any]) -> list[str]:
    if not context:
        return ["missing measurement_context"]
    issues = []
    for key in (
        "host_boot_id",
        "system_generation",
        "kernel_release",
        "cpu_governor",
        "thermal_zone_policy",
    ):
        if _str_or_none(context.get(key)) is None:
            issues.append(f"measurement_context.{key} missing")
    env_digest = context.get("env_digest")
    if not isinstance(env_digest, dict) or not env_digest:
        issues.append("measurement_context.env_digest must be a non-empty object")
    return issues


def _internal_json_path(template: dict[str, Any], *, run_group_id: str, run_id: str) -> str | None:
    path = _str_or_none(template.get("path"))
    if path is None:
        return None
    return path.replace("{run_group_id}", run_group_id).replace("{run_id}", run_id)


__all__ = [
    "BenchmarkManifestValidation",
    "BenchmarkReadiness",
    "BenchmarkManifestRun",
    "BootstrapEstimate",
    "PairedBootstrapEstimate",
    "StratifiedBootstrapEstimate",
    "benchmark_readiness",
    "benchmark_run_manifest",
    "bootstrap_delta_ci",
    "is_controlled_benchmark_manifest",
    "is_template_benchmark_manifest",
    "paired_bootstrap_delta_ci",
    "paired_sign_flip_p_value",
    "permutation_delta_p_value",
    "selected_run_assignment_issues",
    "stratified_bootstrap_delta_ci",
    "stratified_permutation_delta_p_value",
    "validate_executed_benchmark_manifest",
]
