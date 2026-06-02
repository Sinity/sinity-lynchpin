"""Machine-analysis status aggregation over generated artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lynchpin.core.io import load_json_if_exists, resolve_analysis_path

MACHINE_STATUS_ARTIFACTS = (
    "machine_dataset_diagnostics.json",
    "machine_measurement_system.json",
    "machine_experiment_claims.json",
    "machine_support_assessment.json",
    "machine_instrumentation_gaps.json",
    "machine_benchmark_preflight.json",
    "machine_benchmark_execution_queue.json",
    "machine_below_export_queue.json",
    "machine_experiment_manifest_diagnostics.json",
    "machine_attribution_claims.json",
    "machine_assumption_checks.json",
    "machine_analysis_readiness.json",
)


def machine_status_payload(
    *,
    resolver: Callable[[str], str | Path] | None = None,
) -> dict[str, Any]:
    resolver = resolver or resolve_analysis_path
    artifacts = {name: _artifact(name, resolver=resolver) for name in MACHINE_STATUS_ARTIFACTS}
    support = artifacts["machine_support_assessment.json"]
    gaps = artifacts["machine_instrumentation_gaps.json"]
    preflight = artifacts["machine_benchmark_preflight.json"]
    execution_queue = artifacts["machine_benchmark_execution_queue.json"]
    below_export_queue = artifacts["machine_below_export_queue.json"]
    manifest_diagnostics = artifacts["machine_experiment_manifest_diagnostics.json"]
    experiments = artifacts["machine_experiment_claims.json"]
    claims = artifacts["machine_attribution_claims.json"]
    dataset = artifacts["machine_dataset_diagnostics.json"]
    measurement = artifacts["machine_measurement_system.json"]
    assumptions = artifacts["machine_assumption_checks.json"]
    readiness = artifacts["machine_analysis_readiness.json"]
    support_levels = _support_levels(support)
    payload: dict[str, Any] = {
        "artifacts": {
            "expected": len(MACHINE_STATUS_ARTIFACTS),
            "available": sum(1 for row in artifacts.values() if row is not None),
            "missing": [name for name, row in artifacts.items() if row is None],
        },
        "support": {
            "candidate_count": _int(support, "candidate_count"),
            "refusal_count": _int(support, "refusal_count"),
            "executed_controlled_claim_count": _int(support, "controlled_claim_count"),
            "natural_experiment_support_count": _int(support, "natural_experiment_support_count"),
            "assessment_by_support_level": support_levels,
            "assessment_controlled": support_levels.get("controlled", 0),
            "assessment_natural_experiment": support_levels.get("natural_experiment", 0),
            "assessment_insufficient": support_levels.get("insufficient", 0),
            # Backward-compatible aliases for existing MCP/CLI consumers.
            "controlled_claim_count": _int(support, "controlled_claim_count"),
            "controlled": support_levels.get("controlled", 0),
            "natural_experiment": support_levels.get("natural_experiment", 0),
            "insufficient": support_levels.get("insufficient", 0),
        },
        "gaps": _gap_status(gaps),
        "benchmark_preflight": {
            "run_count": _int(preflight, "run_count"),
            "ready_run_count": _int(preflight, "ready_run_count"),
            "issue_count": _int(preflight, "issue_count"),
            "warning_count": _int(preflight, "warning_count"),
        },
        "benchmark_execution_queue": {
            "queue_count": _int(execution_queue, "queue_count"),
            "ready_group_count": _int(execution_queue, "ready_group_count"),
            "blocked_group_count": _int(execution_queue, "blocked_group_count"),
            "run_template_count": _int(execution_queue, "run_template_count"),
            "ready_run_count": _int(execution_queue, "ready_run_count"),
        },
        "below_export_queue": {
            "queue_count": _int(below_export_queue, "queue_count"),
            "failed_capture_count": _int(below_export_queue, "failed_capture_count"),
            "root": below_export_queue.get("root") if isinstance(below_export_queue, dict) else None,
            "live_store": below_export_queue.get("live_store") if isinstance(below_export_queue, dict) else None,
        },
        "experiment_manifests": {
            "manifest_count": _int(manifest_diagnostics, "manifest_count"),
            "source_loadable_count": _int(manifest_diagnostics, "source_loadable_count"),
            "controlled_benchmark_valid_count": _int(
                manifest_diagnostics,
                "controlled_benchmark_valid_count",
            ),
            "validation_issue_count": _int(manifest_diagnostics, "validation_issue_count"),
            "promotion_issue_count": _int(manifest_diagnostics, "promotion_issue_count"),
            "controlled_run_invalid_count": _int(manifest_diagnostics, "controlled_run_invalid_count"),
            "legacy_observational_count": _int(manifest_diagnostics, "legacy_observational_count"),
            "template_count": _int(manifest_diagnostics, "template_count"),
            "out_of_window_count": _int(manifest_diagnostics, "out_of_window_count"),
            "by_kind": _dict_field(manifest_diagnostics, "by_kind"),
        },
        "experiments": {
            "run_count": _int(experiments, "run_count"),
            "controlled": _int(experiments, "controlled_claim_count"),
            "observational": _int(experiments, "observational_claim_count"),
            "by_manifest_validation_status": _manifest_validation_statuses(experiments),
        },
        "claims": {
            "claim_count": _int(claims, "claim_count"),
            "by_support_level": _dict_field(claims, "by_support_level"),
        },
        "dataset": _dataset_status(dataset),
        "measurement": {
            "check_count": _int(measurement, "check_count"),
            "by_status": _dict_field(measurement, "by_status"),
        },
        "assumptions": {
            "check_count": _int(assumptions, "check_count"),
            "by_status": _dict_field(assumptions, "by_status"),
            "failed_by_support_level": _failed_assumption_counts(assumptions, "support_level"),
            "failed_by_scope": _failed_assumption_counts(assumptions, "claim_scope"),
        },
        "readiness": _readiness_status(readiness),
    }
    payload["blockers"] = _blockers(payload)
    return payload


def _artifact(name: str, *, resolver: Callable[[str], str | Path]) -> dict[str, Any] | None:
    payload = load_json_if_exists(Path(resolver(name)))
    return payload if isinstance(payload, dict) else None


def _support_levels(payload: dict[str, Any] | None) -> dict[str, int]:
    levels: dict[str, int] = {}
    for row in _list_field(payload, "assessments"):
        if isinstance(row, dict):
            level = str(row.get("support_level") or "unknown")
            levels[level] = levels.get(level, 0) + 1
    return levels


def _dataset_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    feature = payload.get("feature_audit") if isinstance(payload, dict) else None
    mining = payload.get("mining_audit") if isinstance(payload, dict) else None
    feature = feature if isinstance(feature, dict) else {}
    mining = mining if isinstance(mining, dict) else {}
    return {
        "feature_status": feature.get("status"),
        "multiplicity_status": mining.get("multiplicity_status"),
    }


def _gap_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    rows = _list_field(payload, "gaps")
    next_actions: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("next_action") or "").strip()
        if action:
            next_actions[action] = next_actions.get(action, 0) + 1
    return {
        "gap_count": _int(payload, "gap_count"),
        "by_missing_source": _dict_field(payload, "by_missing_source"),
        "by_mechanism_family": _dict_field(payload, "by_mechanism_family"),
        "by_next_action": dict(sorted(next_actions.items(), key=lambda item: (-item[1], item[0]))),
    }


def _readiness_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    dimensions = _list_field(payload, "dimensions")
    by_status: dict[str, int] = {}
    unstable: list[dict[str, Any]] = []
    for row in dimensions:
        if isinstance(row, dict):
            status = str(row.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            if status != "stable":
                unstable.append({
                    "dimension": row.get("dimension"),
                    "status": status,
                    "caveats": row.get("caveats", []),
                })
    return {
        "dimension_count": len(dimensions),
        "by_status": dict(sorted(by_status.items())),
        "unstable_dimensions": unstable,
    }


def _failed_assumption_counts(payload: dict[str, Any] | None, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in _list_field(payload, "checks"):
        if not isinstance(row, dict) or row.get("check_status") != "failed":
            continue
        key = str(row.get(field) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _manifest_validation_statuses(payload: dict[str, Any] | None) -> dict[str, int]:
    by_status: dict[str, int] = {}
    for row in _list_field(payload, "claim_packs"):
        if not isinstance(row, dict):
            continue
        validation = row.get("manifest_validation")
        valid = validation.get("valid") if isinstance(validation, dict) else None
        status = "valid" if valid is True else "invalid" if valid is False else "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    return dict(sorted(by_status.items()))


def _blockers(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    artifacts = payload["artifacts"] if isinstance(payload["artifacts"], dict) else {}
    for name in artifacts.get("missing", []):
        blockers.append(f"missing artifact: {name}")
    experiments = payload["experiments"] if isinstance(payload["experiments"], dict) else {}
    if int(experiments.get("run_count") or 0) > 0 and int(experiments.get("controlled") or 0) == 0:
        blockers.append("experiment manifests exist but no controlled benchmark claim is currently proven")
    manifests = payload["experiment_manifests"] if isinstance(payload["experiment_manifests"], dict) else {}
    if int(manifests.get("promotion_issue_count") or 0) > 0:
        blockers.append(f"{manifests.get('promotion_issue_count')} experiment manifests are not source-loadable")
    if int(manifests.get("controlled_run_invalid_count") or 0) > 0:
        blockers.append(f"{manifests.get('controlled_run_invalid_count')} executed benchmark manifests are invalid")
    preflight = payload["benchmark_preflight"] if isinstance(payload["benchmark_preflight"], dict) else {}
    if int(preflight.get("issue_count") or 0) > 0:
        blockers.append(f"{preflight.get('issue_count')} benchmark run templates fail preflight")
    support = payload["support"] if isinstance(payload["support"], dict) else {}
    if int(support.get("insufficient") or 0) > 0:
        blockers.append(f"{support.get('insufficient')} support assessments remain explicit refusals")
    dataset = payload["dataset"] if isinstance(payload["dataset"], dict) else {}
    if dataset.get("feature_status") not in {None, "ready_for_mining"}:
        blockers.append(f"dataset feature audit is {dataset.get('feature_status')}")
    if dataset.get("multiplicity_status") not in {None, "registered"}:
        blockers.append(f"dataset multiplicity audit is {dataset.get('multiplicity_status')}")
    measurement = payload["measurement"] if isinstance(payload["measurement"], dict) else {}
    measurement_status = measurement.get("by_status") if isinstance(measurement.get("by_status"), dict) else {}
    if int(measurement_status.get("failed") or 0) > 0:
        blockers.append(f"{measurement_status.get('failed')} measurement-system diagnostics failed")
    assumptions = payload["assumptions"] if isinstance(payload["assumptions"], dict) else {}
    assumption_status = assumptions.get("by_status") if isinstance(assumptions.get("by_status"), dict) else {}
    failed_assumptions = int(assumption_status.get("failed") or 0)
    if failed_assumptions > 0:
        failed_levels = assumptions.get("failed_by_support_level") if isinstance(assumptions.get("failed_by_support_level"), dict) else {}
        supported_failures = sum(int(failed_levels.get(level) or 0) for level in ("controlled", "natural_experiment"))
        if supported_failures:
            blockers.append(f"{supported_failures} supported attribution assumption checks failed")
        refused_failures = int(failed_levels.get("insufficient") or 0)
        unclassified_failures = failed_assumptions - supported_failures - refused_failures
        if unclassified_failures:
            blockers.append(f"{unclassified_failures} unclassified attribution assumption checks failed")
    readiness = payload["readiness"] if isinstance(payload["readiness"], dict) else {}
    for row in readiness.get("unstable_dimensions", []):
        if isinstance(row, dict):
            blockers.append(f"readiness dimension {row.get('dimension') or 'unnamed'} is {row.get('status')}")
    return blockers


def _int(payload: dict[str, Any] | None, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    value = payload.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _dict_field(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}


def _list_field(payload: dict[str, Any] | None, key: str) -> list[Any]:
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, list) else []


__all__ = ["MACHINE_STATUS_ARTIFACTS", "machine_status_payload"]
