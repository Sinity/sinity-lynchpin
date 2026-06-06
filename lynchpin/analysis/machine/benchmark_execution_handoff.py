"""Ranked handoff from benchmark templates to future execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineBenchmarkExecutionHandoffItem:
    handoff_id: str
    candidate_id: str
    run_group_id: str
    plan_id: str
    priority_score: float
    pareto_frontier: bool
    support_level: str | None
    ready_to_export: bool
    run_count: int
    ready_run_count: int
    issue_count: int
    warning_count: int
    treatments: tuple[str, ...]
    cache_conditions: tuple[str, ...]
    derivation_keys: tuple[str, ...]
    primary_metric: str
    export_command: str
    next_action: str
    refusal_reasons: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineBenchmarkExecutionHandoff:
    generated_for: dict[str, Any]
    handoff_count: int
    ready_group_count: int
    blocked_group_count: int
    run_template_count: int
    ready_run_count: int
    items: list[MachineBenchmarkExecutionHandoffItem]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_benchmark_execution_handoff(
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    manifest_bundle_path: Path | None = None,
    preflight_path: Path | None = None,
    support_path: Path | None = None,
    limit: int = 10,
) -> MachineBenchmarkExecutionHandoff:
    candidates = load_json_object(
        candidates_path or resolve_analysis_path("machine_attribution_candidates.json"),
        label="machine attribution candidates",
    )
    bundle = load_json_object(
        manifest_bundle_path or resolve_analysis_path("machine_benchmark_manifest_bundle.json"),
        label="machine benchmark manifest bundle",
    )
    preflight = load_json_object(
        preflight_path or resolve_analysis_path("machine_benchmark_preflight.json"),
        label="machine benchmark preflight",
    )
    support = load_json_object(
        support_path or resolve_analysis_path("machine_support_assessment.json"),
        label="machine support assessment",
    )
    candidate_by_id = {
        str(row.get("candidate_id")): row
        for row in candidates.get("candidates", [])
        if isinstance(row, dict) and row.get("candidate_id")
    }
    support_by_candidate = {
        str(row.get("candidate_id")): row
        for row in support.get("assessments", [])
        if isinstance(row, dict) and row.get("candidate_id")
    }
    preflight_by_group = {
        str(row.get("run_group_id")): row
        for row in preflight.get("groups", [])
        if isinstance(row, dict) and row.get("run_group_id")
    }
    items = [
        item
        for group in bundle.get("groups", [])
        if isinstance(group, dict)
        for item in (
            _handoff_item(
                group,
                candidate=candidate_by_id.get(str(group.get("candidate_id") or "")),
                support=support_by_candidate.get(str(group.get("candidate_id") or "")),
                preflight=preflight_by_group.get(str(group.get("run_group_id") or "")),
            ),
        )
        if item is not None
    ]
    items.sort(key=lambda row: (not row.ready_to_export, not row.pareto_frontier, -row.priority_score, row.run_group_id))
    if limit > 0:
        items = items[:limit]
    return MachineBenchmarkExecutionHandoff(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": [
                "machine_attribution_candidates.json",
                "machine_benchmark_manifest_bundle.json",
                "machine_benchmark_preflight.json",
                "machine_support_assessment.json",
            ],
            "limit": limit,
        },
        handoff_count=len(items),
        ready_group_count=sum(1 for row in items if row.ready_to_export),
        blocked_group_count=sum(1 for row in items if not row.ready_to_export),
        run_template_count=sum(row.run_count for row in items),
        ready_run_count=sum(row.ready_run_count for row in items),
        items=items,
        caveats=[
            "execution handoff is a ranked handoff only; it does not run benchmarks",
            "ready_to_export means template/preflight readiness, not causal support",
        ],
    )


def write_machine_benchmark_execution_handoff(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    candidates_path: Path | None = None,
    manifest_bundle_path: Path | None = None,
    preflight_path: Path | None = None,
    support_path: Path | None = None,
    limit: int = 10,
) -> MachineBenchmarkExecutionHandoff:
    analysis = analyze_machine_benchmark_execution_handoff(
        start=start,
        end=end,
        candidates_path=candidates_path,
        manifest_bundle_path=manifest_bundle_path,
        preflight_path=preflight_path,
        support_path=support_path,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _handoff_item(
    group: dict[str, Any],
    *,
    candidate: dict[str, Any] | None,
    support: dict[str, Any] | None,
    preflight: dict[str, Any] | None,
) -> MachineBenchmarkExecutionHandoffItem | None:
    run_group_id = str(group.get("run_group_id") or "")
    candidate_id = str(group.get("candidate_id") or "")
    if not run_group_id or not candidate_id:
        return None
    run_templates = [row for row in group.get("run_templates", []) if isinstance(row, dict)]
    preflight = preflight or {}
    run_count = int(preflight.get("run_count") or group.get("run_count") or len(run_templates))
    ready_run_count = int(preflight.get("ready_run_count") or 0)
    issue_count = int(preflight.get("issue_count") or 0)
    warning_count = int(preflight.get("warning_count") or 0)
    derivation_keys = tuple(
        sorted({
            str(row.get("derivation_key"))
            for row in run_templates
            if row.get("derivation_key")
        })
    )
    support = support or {}
    next_action = _next_action(support)
    ready = run_count > 0 and ready_run_count == run_count and issue_count == 0
    return MachineBenchmarkExecutionHandoffItem(
        handoff_id=f"machine-benchmark-handoff:{run_group_id}",
        candidate_id=candidate_id,
        run_group_id=run_group_id,
        plan_id=str(group.get("plan_id") or ""),
        priority_score=float((candidate or {}).get("priority_score") or 0.0),
        pareto_frontier=bool((candidate or {}).get("pareto_frontier")),
        support_level=str(support.get("support_level")) if support.get("support_level") else None,
        ready_to_export=ready,
        run_count=run_count,
        ready_run_count=ready_run_count,
        issue_count=issue_count,
        warning_count=warning_count,
        treatments=tuple(str(item) for item in preflight.get("treatments", ()) if item),
        cache_conditions=tuple(str(item) for item in preflight.get("cache_conditions", ()) if item),
        derivation_keys=derivation_keys,
        primary_metric=str(group.get("primary_metric") or (candidate or {}).get("metric") or ""),
        export_command="python -m lynchpin.analysis machine-benchmark-export --out <output-dir>",
        next_action=next_action,
        refusal_reasons=tuple(str(item) for item in support.get("refusal_reasons", ()) if item),
        caveats=tuple(str(item) for item in group.get("caveats", ()) if item),
    )


def _next_action(support: dict[str, Any]) -> str:
    for gap in support.get("instrumentation_gaps", ()):
        if isinstance(gap, dict) and gap.get("next_action"):
            return str(gap["next_action"])
    if support.get("support_level") == "insufficient":
        return "execute the approved manifest and promote run logs/telemetry"
    return "export manifest templates, execute runs, then materialize machine analysis"


__all__ = [
    "MachineBenchmarkExecutionHandoff",
    "MachineBenchmarkExecutionHandoffItem",
    "analyze_machine_benchmark_execution_handoff",
    "write_machine_benchmark_execution_handoff",
]
