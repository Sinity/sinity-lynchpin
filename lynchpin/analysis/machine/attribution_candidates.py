"""Machine attribution candidate generation from existing observations.

Candidates are not causal claims. They are a ranked set of extant observational
patterns worth turning into controlled benchmark manifests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_materialized_analysis_artifact, load_json_if_exists, load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineAttributionCandidate:
    candidate_id: str
    project: str | None
    metric: str
    suspected_factor: str
    mechanism_family: str | None
    support_ceiling: str
    priority_score: float
    score_components: dict[str, float]
    summary: str
    source_artifacts: tuple[str, ...]
    source_ids: tuple[str, ...]
    suggested_benchmark_manifest: dict[str, Any]
    caveats: tuple[str, ...]
    discovery_window: dict[str, Any] | None = None
    validation_status: str = "unvalidated_candidate"
    mining_scan_id: str | None = None
    rank_within_scan: int | None = None
    pareto_frontier: bool = False


@dataclass(frozen=True)
class MachineAttributionCandidateAnalysis:
    generated_for: dict[str, Any]
    candidate_count: int
    pareto_frontier_count: int
    pareto_frontier_ids: tuple[str, ...]
    candidates: list[MachineAttributionCandidate]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_attribution_candidates(
    *,
    start: date | None = None,
    end: date | None = None,
    deltas_path: Path | None = None,
    work_observations_path: Path | None = None,
    mining_path: Path | None = None,
    comparisons_path: Path | None = None,
    matched_designs_path: Path | None = None,
    limit: int = 25,
) -> MachineAttributionCandidateAnalysis:
    deltas_payload = load_json_object(
        deltas_path or resolve_analysis_path("machine_observational_deltas.json"),
        label="machine observational deltas",
    )
    work_payload = load_json_object(
        work_observations_path or resolve_analysis_path("machine_work_observations.json"),
        label="machine work observations",
    )
    mining_payload = _optional_payload(
        mining_path,
        default_name="machine_mining.json",
    )
    comparisons_payload = (
        _optional_payload(comparisons_path, default_name="machine_comparisons.json")
        if comparisons_path is not None or (deltas_path is None and work_observations_path is None and mining_path is None)
        else None
    )
    matched_payload = (
        _optional_payload(matched_designs_path, default_name="machine_matched_designs.json")
        if matched_designs_path is not None or (deltas_path is None and work_observations_path is None and mining_path is None)
        else None
    )
    candidates = [
        *_matched_design_candidates(matched_payload),
        *_comparison_candidates(comparisons_payload),
        *_lagged_exposure_candidates(mining_payload),
        *_anomaly_cluster_candidates(mining_payload),
        *_mining_candidates(mining_payload),
        *_delta_candidates(deltas_payload),
        *_failure_candidates(work_payload),
        *_test_candidates(work_payload),
        *_stage_candidates(work_payload),
    ]
    candidates = _mark_pareto_frontier(candidates)
    candidates = _candidate_set(candidates, limit=limit)
    frontier_ids = tuple(row.candidate_id for row in candidates if row.pareto_frontier)
    caveats = [
        "candidate set only; no causal claim is emitted",
        "controlled support requires a future randomized manifest with fixed derivations and telemetry linkage",
        "candidate selection is stratified because raw priority scores are not comparable across mining families",
        "pareto_frontier marks candidates not dominated on effect, recurrence, validation, and controllability components",
    ]
    return MachineAttributionCandidateAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": [
                "machine_observational_deltas.json",
                "machine_work_observations.json",
                "machine_mining.json",
                "machine_comparisons.json",
                "machine_matched_designs.json",
            ],
            "limit": limit,
        },
        candidate_count=len(candidates),
        pareto_frontier_count=len(frontier_ids),
        pareto_frontier_ids=frontier_ids,
        candidates=candidates,
        caveats=caveats,
    )


def _candidate_set(
    candidates: list[MachineAttributionCandidate],
    *,
    limit: int,
) -> list[MachineAttributionCandidate]:
    ordered = sorted(candidates, key=_candidate_sort_key)
    if limit <= 0 or len(ordered) <= limit:
        return ordered

    natural = [row for row in ordered if row.support_ceiling == "natural_experiment_design"]
    other = [row for row in ordered if row.support_ceiling != "natural_experiment_design"]
    natural_quota = min(len(natural), max(1, limit // 2))
    selected = [*natural[:natural_quota]]
    selected_ids = {row.candidate_id for row in selected}
    for row in _family_frontier(other):
        if len(selected) >= limit:
            break
        if row.candidate_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row.candidate_id)
    selected.extend(row for row in other if row.candidate_id not in selected_ids)
    if len(selected) < limit:
        selected.extend(row for row in ordered if row not in selected)
    return sorted(selected[:limit], key=_candidate_sort_key)


def _family_frontier(candidates: list[MachineAttributionCandidate]) -> list[MachineAttributionCandidate]:
    by_family: dict[str, MachineAttributionCandidate] = {}
    for row in candidates:
        family = row.mechanism_family or "unknown"
        current = by_family.get(family)
        if current is None or _candidate_sort_key(row) < _candidate_sort_key(current):
            by_family[family] = row
    return sorted(by_family.values(), key=_candidate_sort_key)


def _candidate_sort_key(row: MachineAttributionCandidate) -> tuple[int, float, str, str]:
    support_rank = 0 if row.support_ceiling == "natural_experiment_design" else 1
    return (support_rank, -row.priority_score, row.metric, row.suspected_factor)


def _mark_pareto_frontier(candidates: list[MachineAttributionCandidate]) -> list[MachineAttributionCandidate]:
    frontier_ids = {
        row.candidate_id
        for row in candidates
        if not any(_dominates(other, row) for other in candidates if other.candidate_id != row.candidate_id)
    }
    return [replace(row, pareto_frontier=row.candidate_id in frontier_ids) for row in candidates]


def _dominates(left: MachineAttributionCandidate, right: MachineAttributionCandidate) -> bool:
    left_vector = _value_vector(left)
    right_vector = _value_vector(right)
    return all(left_value >= right_value for left_value, right_value in zip(left_vector, right_vector, strict=True)) and any(
        left_value > right_value for left_value, right_value in zip(left_vector, right_vector, strict=True)
    )


def _value_vector(row: MachineAttributionCandidate) -> tuple[float, float, float, float]:
    components = row.score_components
    return (
        float(components.get("effect_size") or 0.0),
        float(components.get("recurrence") or 0.0),
        float(components.get("validation_strength") or 0.0),
        _controllability_score(row),
    )


def _controllability_score(row: MachineAttributionCandidate) -> float:
    if row.support_ceiling == "natural_experiment_design":
        return 0.75
    manifest = row.suggested_benchmark_manifest.get("controlled_benchmark")
    return 1.0 if isinstance(manifest, dict) else 0.0


def write_machine_attribution_candidates(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    deltas_path: Path | None = None,
    work_observations_path: Path | None = None,
    mining_path: Path | None = None,
    comparisons_path: Path | None = None,
    matched_designs_path: Path | None = None,
    limit: int = 25,
) -> MachineAttributionCandidateAnalysis:
    analysis = analyze_machine_attribution_candidates(
        start=start,
        end=end,
        deltas_path=deltas_path,
        work_observations_path=work_observations_path,
        mining_path=mining_path,
        comparisons_path=comparisons_path,
        matched_designs_path=matched_designs_path,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _mining_candidates(payload: dict[str, Any] | None) -> list[MachineAttributionCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("cohorts")
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        max_outcome = _float(row.get("max_outcome"))
        p95 = _float(row.get("p95_outcome"))
        count = int(row.get("row_count") or 0)
        if count < 2 or max(max_outcome, p95) <= 0:
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        stage = str(dimensions.get("stage_name") or "unknown")
        project = str(dimensions.get("project")) if dimensions.get("project") else None
        scan_id = str(row.get("scan_id") or "unknown-scan")
        metric = "stage.duration_s"
        factor = f"mined_cohort:stage={stage}"
        score = max(max_outcome, p95) * count
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, project, scan_id),
                project=project,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="unknown_mined_stage_slowdown",
                support_ceiling="candidate",
                priority_score=round(score, 3),
                score_components={
                    "effect_size": round(max(max_outcome, p95), 3),
                    "recurrence": float(count),
                    "search_universe_size": float(_scan_universe(payload)),
                    "validation_strength": 0.0,
                },
                summary=f"mined cohort {stage}/{project or 'unknown'} has {count} rows; p95={p95}s max={max_outcome}s",
                source_artifacts=("machine_mining.json",),
                source_ids=tuple(str(value) for value in (row.get("cohort_id"), scan_id) if value),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status="discovery_only",
                mining_scan_id=scan_id,
                rank_within_scan=rank,
            )
        )
    return result


def _comparison_candidates(payload: dict[str, Any] | None) -> list[MachineAttributionCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("contrasts")
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        treated_n = int(row.get("treated_n") or 0)
        comparison_n = int(row.get("comparison_n") or 0)
        delta = abs(_float(row.get("median_delta")))
        if treated_n < 2 or comparison_n < 2 or delta <= 0:
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        stage = str(dimensions.get("stage_name") or "unknown")
        project = str(dimensions.get("project")) if dimensions.get("project") else None
        q_value = _float(row.get("q_value"))
        validation_strength = 1.0 if row.get("statistical_signal") == "screening_signal" else 0.25
        score = delta * treated_n * max(validation_strength, 0.1)
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id("contrast", row.get("contrast_id"), project),
                project=project,
                metric=str(row.get("outcome_metric") or "stage.duration_s"),
                suspected_factor=f"cohort_contrast:stage={stage}",
                mechanism_family="observational_stage_contrast",
                support_ceiling="candidate",
                priority_score=round(score, 3),
                score_components={
                    "effect_size": round(delta, 3),
                    "recurrence": float(treated_n),
                    "search_universe_size": float(payload.get("contrast_count") or 0),
                    "validation_strength": validation_strength,
                    "q_value": q_value,
                },
                summary=(
                    f"{stage}/{project or 'unknown'} median differs from rest of frame "
                    f"by {row.get('median_delta')}s (q={row.get('q_value')})"
                ),
                source_artifacts=("machine_comparisons.json",),
                source_ids=tuple(str(value) for value in (row.get("contrast_id"), row.get("cohort_id"), row.get("scan_id")) if value),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=str(row.get("outcome_metric") or "stage.duration_s"),
                    treatment_label=f"cohort_contrast:{stage}",
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status=str(row.get("statistical_signal") or "screened_contrast"),
                mining_scan_id=str(row.get("scan_id")) if row.get("scan_id") else None,
                rank_within_scan=rank,
            )
        )
    return result


def _lagged_exposure_candidates(payload: dict[str, Any] | None) -> list[MachineAttributionCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("lagged_exposures")
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        delta = abs(_float(row.get("median_delta")))
        paired = int(row.get("paired_count") or 0)
        high = int(row.get("high_prior_pressure_count") or 0)
        if paired < 2 or high < 1 or delta <= 0:
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        stage = str(dimensions.get("stage_name") or "unknown")
        project = str(dimensions.get("project")) if dimensions.get("project") else None
        metric = "stage.duration_s"
        factor = f"lagged_pressure:{row.get('pressure_metric')}"
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, project, row.get("summary_id")),
                project=project,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="lagged_pressure_exposure",
                support_ceiling="candidate",
                priority_score=round(delta * paired, 3),
                score_components={
                    "effect_size": round(delta, 3),
                    "recurrence": float(paired),
                    "search_universe_size": float(payload.get("lagged_exposure_count") or 0),
                    "validation_strength": 0.0,
                },
                summary=(
                    f"{stage}/{project or 'unknown'} has lagged {row.get('pressure_metric')} "
                    f"association: median_delta={row.get('median_delta')}s across {paired} paired rows"
                ),
                source_artifacts=("machine_mining.json",),
                source_ids=tuple(str(value) for value in (row.get("summary_id"),) if value),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status="temporal_precedence_screen",
                mining_scan_id=_scan_id(payload),
                rank_within_scan=rank,
            )
        )
    return result


def _anomaly_cluster_candidates(payload: dict[str, Any] | None) -> list[MachineAttributionCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("anomaly_clusters")
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        anomaly_count = int(row.get("anomaly_count") or 0)
        max_outcome = _float(row.get("max_outcome"))
        if anomaly_count < 2 or max_outcome <= 0:
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        stage = str(dimensions.get("stage_name") or "unknown")
        project = str(dimensions.get("project")) if dimensions.get("project") else None
        signature = ",".join(str(item) for item in row.get("pressure_signature", ()) if item) or "tail_duration"
        metric = "stage.duration_s"
        factor = f"anomaly_cluster:{signature}"
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, project, row.get("cluster_id")),
                project=project,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="machine_context_anomaly_cluster",
                support_ceiling="candidate",
                priority_score=round(max_outcome * anomaly_count, 3),
                score_components={
                    "effect_size": round(max_outcome, 3),
                    "recurrence": float(anomaly_count),
                    "search_universe_size": float(payload.get("anomaly_cluster_count") or 0),
                    "validation_strength": 0.0,
                },
                summary=(
                    f"{stage}/{project or 'unknown'} has {anomaly_count} recurring tail rows "
                    f"with max={max_outcome}s and signature={signature}"
                ),
                source_artifacts=("machine_mining.json",),
                source_ids=tuple(str(value) for value in (row.get("cluster_id"),) if value),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status="tail_cluster_screen",
                mining_scan_id=_scan_id(payload),
                rank_within_scan=rank,
            )
        )
    return result


def _matched_design_candidates(payload: dict[str, Any] | None) -> list[MachineAttributionCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("designs")
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        did = abs(_float(row.get("difference_in_differences")))
        treated_n = int(row.get("treated_before_n") or 0) + int(row.get("treated_after_n") or 0)
        control_n = int(row.get("control_before_n") or 0) + int(row.get("control_after_n") or 0)
        if did <= 0 or min(treated_n, control_n) < 4:
            continue
        stage = str(row.get("stage_name") or "unknown")
        project = str(row.get("project")) if row.get("project") else None
        boundary_type = str(row.get("boundary_type") or "git_commit_transition")
        factor = _boundary_factor(boundary_type=boundary_type, stage=stage)
        ready_bonus = 2.0 if row.get("identification_status") == "design_ready" else 0.5
        negative_bonus = 1.25 if row.get("negative_control_status") == "passed" else 0.5
        score = did * min(treated_n, control_n) * ready_bonus * negative_bonus
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id("matched-design", row.get("design_id"), project),
                project=project,
                metric=str(row.get("outcome_metric") or "stage.duration_s"),
                suspected_factor=factor,
                mechanism_family="natural_experiment_boundary",
                support_ceiling=str(row.get("support_ceiling") or "candidate"),
                priority_score=round(score, 3),
                score_components={
                    "effect_size": round(did, 3),
                    "recurrence": float(min(treated_n, control_n)),
                    "search_universe_size": float(payload.get("design_count") or 0),
                    "validation_strength": ready_bonus * negative_bonus,
                    "placebo_abs_delta": abs(_float(row.get("placebo_delta"))),
                },
                summary=(
                    f"{stage}/{project or 'unknown'} boundary matched against {row.get('control_family')} "
                    f"has diff-in-diff={row.get('difference_in_differences')}s "
                    f"({row.get('identification_status')}, negative={row.get('negative_control_status')})"
                ),
                source_artifacts=("machine_matched_designs.json",),
                source_ids=tuple(str(value) for value in (row.get("design_id"), row.get("boundary_id")) if value),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=str(row.get("outcome_metric") or "stage.duration_s"),
                    treatment_label=factor,
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status=str(row.get("identification_status") or "matched_design_screen"),
                mining_scan_id=None,
                rank_within_scan=rank,
            )
        )
    return result


def _boundary_factor(*, boundary_type: str, stage: str) -> str:
    if boundary_type == "temporal_run_gap_transition":
        return f"temporal_gap_boundary:{stage}"
    if boundary_type == "git_commit_transition":
        return f"git_boundary:{stage}"
    return f"{boundary_type or 'boundary'}:{stage}"


def _delta_candidates(payload: dict[str, Any]) -> list[MachineAttributionCandidate]:
    rows = payload.get("deltas") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        delta = _float(row.get("median_delta_seconds"))
        p95_delta = _float(row.get("p95_delta_seconds"))
        if delta <= 0 and p95_delta <= 0:
            continue
        tool = str(row.get("tool") or "unknown")
        work_state = str(row.get("work_state") or "unknown")
        pressure_state = str(row.get("pressure_state") or "unknown")
        metric = f"command.{tool}.duration_seconds"
        factor = f"machine_pressure_state={pressure_state}"
        score = max(delta, p95_delta, 0.0) * max(int(row.get("pressure_count") or 1), 1)
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, work_state),
                project=None,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="machine_pressure_association",
                support_ceiling="candidate",
                priority_score=round(score, 3),
                score_components={
                    "effect_size": round(max(delta, p95_delta, 0.0), 3),
                    "recurrence": float(max(int(row.get("pressure_count") or 1), 1)),
                    "search_universe_size": 0.0,
                    "validation_strength": 0.0,
                },
                summary=(
                    f"{tool}/{work_state} is slower under {pressure_state}: "
                    f"median_delta={delta}s p95_delta={p95_delta}s"
                ),
                source_artifacts=("machine_observational_deltas.json",),
                source_ids=(),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"{tool}:{work_state}",
                    metric=metric,
                    treatment_label=pressure_state,
                ),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
                discovery_window=discovery_window,
                validation_status="observational_pressure_delta",
                rank_within_scan=rank,
            )
        )
    return result


def _stage_candidates(payload: dict[str, Any]) -> list[MachineAttributionCandidate]:
    rows = payload.get("stage_summaries") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        p95 = _float(row.get("p95_duration_s"))
        maximum = _float(row.get("max_duration_s"))
        count = int(row.get("observation_count") or 0)
        if count < 2 or max(p95, maximum) <= 0:
            continue
        stage = str(row.get("stage_name") or "unknown")
        metric = f"xtask.stage.{stage}.duration_s"
        factor = "candidate_regression_or_contention"
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, stage),
                project=None,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="stage_regression_or_contention",
                support_ceiling="candidate",
                priority_score=round(max(p95, maximum) * count, 3),
                score_components={
                    "effect_size": round(max(p95, maximum), 3),
                    "recurrence": float(count),
                    "search_universe_size": 0.0,
                    "validation_strength": 0.0,
                },
                summary=f"{stage} stage has {count} observations; p95={p95}s max={maximum}s",
                source_artifacts=("machine_work_observations.json",),
                source_ids=(),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-stage:{stage}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=("stage timing is observational; command mix may differ",),
                discovery_window=discovery_window,
                validation_status="work_summary_only",
                rank_within_scan=rank,
            )
        )
    return result


def _test_candidates(payload: dict[str, Any]) -> list[MachineAttributionCandidate]:
    rows = payload.get("test_summaries") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        p95 = _float(row.get("p95_duration_s"))
        maximum = _float(row.get("max_duration_s"))
        count = int(row.get("test_count") or 0)
        if count < 2 or max(p95, maximum) <= 0:
            continue
        package = str(row.get("package") or "unknown")
        status = str(row.get("status") or "unknown")
        metric = f"xtask.test.{package}.duration_s"
        factor = f"slow_test_package:{package}:status={status}"
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, package, status),
                project=None,
                metric=metric,
                suspected_factor=factor,
                mechanism_family="test_package_tail_latency",
                support_ceiling="candidate",
                priority_score=round(max(p95, maximum) * count, 3),
                score_components={
                    "effect_size": round(max(p95, maximum), 3),
                    "recurrence": float(count),
                    "search_universe_size": 0.0,
                    "validation_strength": 0.0,
                },
                summary=f"{package}/{status} has {count} test rows; p95={p95}s max={maximum}s",
                source_artifacts=("machine_work_observations.json",),
                source_ids=(f"machine-work-test-summary:{package}:{status}",),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-test-package:{package}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=("test rows are observational and grouped by package/status, not a fixed randomized workload",),
                discovery_window=discovery_window,
                validation_status="work_summary_only",
                rank_within_scan=rank,
            )
        )
    return result


def _failure_candidates(payload: dict[str, Any]) -> list[MachineAttributionCandidate]:
    rows = payload.get("failure_summaries") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[MachineAttributionCandidate] = []
    discovery_window = _discovery_window(payload)
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        failures = int(row.get("failure_count") or 0)
        affected = int(row.get("affected_invocation_count") or 0)
        maximum = _float(row.get("max_duration_s"))
        if failures < 1:
            continue
        failure_kind = str(row.get("failure_kind") or "unknown")
        project = str(row.get("project")) if row.get("project") else None
        package = str(row.get("package")) if row.get("package") else None
        stage = str(row.get("stage_name")) if row.get("stage_name") else None
        status = str(row.get("status")) if row.get("status") else None
        failure_type = str(row.get("failure_type")) if row.get("failure_type") else None
        exit_code = str(row.get("exit_code")) if row.get("exit_code") is not None else None
        locus = package or stage or project or "unknown"
        signature = ":".join(part for part in (failure_kind, locus, status, failure_type, exit_code) if part)
        metric = "work.failure_count"
        factor = f"failure_concentration:{signature}"
        recurrence = float(max(failures, affected, 1))
        result.append(
            MachineAttributionCandidate(
                candidate_id=_candidate_id(metric, factor, project, package, stage, status, failure_type, exit_code),
                project=project,
                metric=metric,
                suspected_factor=factor,
                mechanism_family=f"{failure_kind}_failure_concentration",
                support_ceiling="candidate",
                priority_score=round(recurrence * max(maximum, 1.0), 3),
                score_components={
                    "effect_size": round(max(maximum, 1.0), 3),
                    "recurrence": recurrence,
                    "search_universe_size": float(len(rows)),
                    "validation_strength": 0.0,
                },
                summary=(
                    f"{failure_kind} failures concentrate at {locus}: "
                    f"{failures} failures across {affected} invocations"
                ),
                source_artifacts=("machine_work_observations.json",),
                source_ids=(f"machine-work-failure-summary:{signature}",),
                suggested_benchmark_manifest=_suggested_manifest(
                    workload=f"xtask-failure-repro:{locus}",
                    metric=metric,
                    treatment_label=factor,
                ),
                caveats=("failure concentration is observational and may reflect command mix or intentionally failing attempts",),
                discovery_window=discovery_window,
                validation_status="failure_taxonomy_only",
                rank_within_scan=rank,
            )
        )
    return result


def _suggested_manifest(*, workload: str, metric: str, treatment_label: str) -> dict[str, Any]:
    return {
        "controlled_benchmark": {
            "run_group_id": "<fill-run-group-id>",
            "derivations": [{"name": "<fixed-derivation>", "drv_path": "<nix-drv-path>"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": "<fill-random-seed>",
            "randomized_order": [],
            "control_label": "baseline",
            "treatment_label": treatment_label,
            "internal_json": {
                "path": "<nix-internal-json-path>",
                "log_format": "internal-json",
                "capture_stream": "stderr",
                "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
            },
            "telemetry": {"window_source": "manifest_timestamps"},
            "metric": metric,
        },
        "workload": workload,
    }


def _candidate_id(*parts: object) -> str:
    digest = hashlib.sha1("\0".join(str(part) for part in parts).encode()).hexdigest()[:16]
    return f"machine-candidate:{digest}"


def _float(value: object) -> float:
    try:
        return float(str(value or 0.0))
    except ValueError:
        return 0.0


def _optional_payload(path: Path | None, *, default_name: str) -> dict[str, Any] | None:
    if path is not None:
        loaded = load_json_if_exists(path)
    else:
        loaded, _materialization = load_materialized_analysis_artifact(default_name)
    return loaded if isinstance(loaded, dict) else None


def _discovery_window(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    generated_for = payload.get("generated_for")
    if isinstance(generated_for, dict):
        return {
            "start": generated_for.get("start"),
            "end": generated_for.get("end"),
            "source": generated_for.get("source"),
        }
    window = payload.get("window")
    if isinstance(window, dict):
        return {
            "start": window.get("start"),
            "end": window.get("end"),
            "source": payload.get("refresh_id") or "artifact_window",
        }
    return None


def _scan_id(payload: dict[str, Any] | None) -> str | None:
    scan = payload.get("scan") if isinstance(payload, dict) and isinstance(payload.get("scan"), dict) else {}
    value = scan.get("scan_id")
    return str(value) if value else None


def _scan_universe(payload: dict[str, Any]) -> int:
    scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    return int(scan.get("comparison_universe_size") or 0)


__all__ = [
    "MachineAttributionCandidate",
    "MachineAttributionCandidateAnalysis",
    "analyze_machine_attribution_candidates",
    "write_machine_attribution_candidates",
]
