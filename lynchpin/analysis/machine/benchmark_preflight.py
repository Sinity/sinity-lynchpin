"""Preflight checks for controlled-benchmark run templates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json

from .controlled_benchmarks import benchmark_readiness, selected_run_assignment_issues


@dataclass(frozen=True)
class MachineBenchmarkRunPreflight:
    run_group_id: str
    run_id: str
    sequence_index: int
    treatment_label: str
    cache_condition: str
    derivation_key: str | None
    ready_to_export: bool
    issues: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MachineBenchmarkGroupPreflight:
    run_group_id: str
    candidate_id: str
    plan_id: str
    run_count: int
    ready_run_count: int
    issue_count: int
    warning_count: int
    treatments: tuple[str, ...]
    cache_conditions: tuple[str, ...]
    runs: tuple[MachineBenchmarkRunPreflight, ...]


@dataclass(frozen=True)
class MachineBenchmarkPreflightAnalysis:
    generated_for: dict[str, Any]
    group_count: int
    run_count: int
    ready_run_count: int
    issue_count: int
    warning_count: int
    groups: list[MachineBenchmarkGroupPreflight]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_benchmark_preflight(
    *,
    start: date | None = None,
    end: date | None = None,
    manifest_bundle_path: Path | None = None,
) -> MachineBenchmarkPreflightAnalysis:
    payload = load_json_object(
        manifest_bundle_path or resolve_analysis_path("machine_benchmark_manifest_bundle.json"),
        label="machine benchmark manifest bundle",
    )
    groups = [
        group
        for row in payload.get("groups", [])
        if isinstance(row, dict)
        for group in (_group_preflight(row),)
        if group is not None
    ]
    return MachineBenchmarkPreflightAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_benchmark_manifest_bundle.json",
        },
        group_count=len(groups),
        run_count=sum(group.run_count for group in groups),
        ready_run_count=sum(group.ready_run_count for group in groups),
        issue_count=sum(group.issue_count for group in groups),
        warning_count=sum(group.warning_count for group in groups),
        groups=groups,
        caveats=[
            "preflight validates templates before execution; it does not run benchmarks",
            "templated internal-json paths are warnings until export materializes per-run directories",
        ],
    )


def write_machine_benchmark_preflight(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    manifest_bundle_path: Path | None = None,
) -> MachineBenchmarkPreflightAnalysis:
    analysis = analyze_machine_benchmark_preflight(
        start=start,
        end=end,
        manifest_bundle_path=manifest_bundle_path,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _group_preflight(row: dict[str, Any]) -> MachineBenchmarkGroupPreflight | None:
    run_group_id = str(row.get("run_group_id") or "")
    run_templates = [item for item in row.get("run_templates", []) if isinstance(item, dict)]
    if not run_group_id or not run_templates:
        return None
    runs = tuple(_run_preflight(run_group_id=run_group_id, row=item) for item in run_templates)
    return MachineBenchmarkGroupPreflight(
        run_group_id=run_group_id,
        candidate_id=str(row.get("candidate_id") or ""),
        plan_id=str(row.get("plan_id") or ""),
        run_count=len(runs),
        ready_run_count=sum(1 for run in runs if run.ready_to_export),
        issue_count=sum(len(run.issues) for run in runs),
        warning_count=sum(len(run.warnings) for run in runs),
        treatments=tuple(sorted({run.treatment_label for run in runs if run.treatment_label})),
        cache_conditions=tuple(sorted({run.cache_condition for run in runs if run.cache_condition})),
        runs=runs,
    )


def _run_preflight(*, run_group_id: str, row: dict[str, Any]) -> MachineBenchmarkRunPreflight:
    manifest = _dict(row.get("manifest"))
    planned = _dict(manifest.get("planned_treatment"))
    readiness = benchmark_readiness(planned)
    selected = _dict(planned.get("selected_run"))
    issues: list[str] = []
    warnings: list[str] = []
    if manifest.get("schema") != "lynchpin.machine_experiment.template.v1":
        issues.append("manifest schema is not template.v1")
    if manifest.get("template_status") != "planned_not_executed":
        issues.append("template_status must be planned_not_executed")
    if not readiness.controlled:
        issues.extend(f"controlled benchmark contract gap: {issue}" for issue in readiness.issues)
    issues.extend(
        selected_run_assignment_issues(
            planned,
            payload_run_id=str(manifest.get("run_id") or row.get("run_id") or ""),
            payload_run_group_id=str(manifest.get("run_group_id") or run_group_id),
        )
    )
    if _contains_template(selected.get("internal_json_path")):
        warnings.append("internal-json path is templated until export")
    if _contains_template(readiness.internal_json_path):
        warnings.append("controlled_benchmark.internal_json.path is templated until export")
    return MachineBenchmarkRunPreflight(
        run_group_id=run_group_id,
        run_id=str(row.get("run_id") or manifest.get("run_id") or ""),
        sequence_index=int(row.get("sequence_index") or selected.get("sequence_index") or 0),
        treatment_label=str(row.get("treatment_label") or selected.get("treatment_label") or ""),
        cache_condition=str(row.get("cache_condition") or selected.get("cache_condition") or ""),
        derivation_key=str(row.get("derivation_key") or selected.get("derivation_key"))
        if row.get("derivation_key") or selected.get("derivation_key")
        else None,
        ready_to_export=not issues,
        issues=tuple(dict.fromkeys(issues)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _contains_template(value: object) -> bool:
    return isinstance(value, str) and "{" in value and "}" in value


__all__ = [
    "MachineBenchmarkGroupPreflight",
    "MachineBenchmarkPreflightAnalysis",
    "MachineBenchmarkRunPreflight",
    "analyze_machine_benchmark_preflight",
    "write_machine_benchmark_preflight",
]
