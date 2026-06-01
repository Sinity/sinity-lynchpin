"""First-class instrumentation gaps for machine attribution candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineInstrumentationGapRecord:
    gap_id: str
    candidate_id: str
    assessment_id: str
    mechanism_id: str
    project: str | None
    metric: str
    suspected_factor: str
    mechanism_family: str
    missing: str
    missing_source: str
    missing_window: str
    why_it_matters: str
    next_action: str
    support_blocked_at: str
    blocked_claim_decision: str
    source_artifacts: tuple[str, ...]
    refusal_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MachineInstrumentationGapAnalysis:
    generated_for: dict[str, Any]
    gap_count: int
    by_missing_source: dict[str, int]
    by_mechanism_family: dict[str, int]
    gaps: list[MachineInstrumentationGapRecord]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_instrumentation_gaps(
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
) -> MachineInstrumentationGapAnalysis:
    payload = load_json_object(
        support_assessment_path or resolve_analysis_path("machine_support_assessment.json"),
        label="machine support assessment",
    )
    gaps: list[MachineInstrumentationGapRecord] = []
    for assessment in payload.get("assessments", []):
        if not isinstance(assessment, dict):
            continue
        gaps.extend(_gaps_for_assessment(assessment))
    gaps.sort(key=lambda row: (row.project or "", row.mechanism_family, row.missing_source, row.gap_id))
    return MachineInstrumentationGapAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_support_assessment.json",
        },
        gap_count=len(gaps),
        by_missing_source=_counts(row.missing_source for row in gaps),
        by_mechanism_family=_counts(row.mechanism_family for row in gaps),
        gaps=gaps,
        caveats=[
            "instrumentation gaps explain why support cannot be upgraded; they are not evidence of an effect",
            "gap priority is inherited from candidate/support-assessment ordering",
        ],
    )


def write_machine_instrumentation_gaps(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
) -> MachineInstrumentationGapAnalysis:
    analysis = analyze_machine_instrumentation_gaps(
        start=start,
        end=end,
        support_assessment_path=support_assessment_path,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _gaps_for_assessment(assessment: dict[str, Any]) -> list[MachineInstrumentationGapRecord]:
    mechanism = assessment.get("mechanism") if isinstance(assessment.get("mechanism"), dict) else {}
    result = []
    for gap in assessment.get("instrumentation_gaps", []):
        if not isinstance(gap, dict):
            continue
        result.append(
            MachineInstrumentationGapRecord(
                gap_id=str(gap.get("gap_id") or ""),
                candidate_id=str(assessment.get("candidate_id") or gap.get("candidate_id") or ""),
                assessment_id=str(assessment.get("assessment_id") or ""),
                mechanism_id=str(mechanism.get("mechanism_id") or ""),
                project=str(assessment.get("project")) if assessment.get("project") else None,
                metric=str(assessment.get("metric") or "unknown_metric"),
                suspected_factor=str(assessment.get("suspected_factor") or "unknown_factor"),
                mechanism_family=str(mechanism.get("mechanism_family") or "unknown"),
                missing=str(gap.get("missing") or "unknown"),
                missing_source=_missing_source(str(gap.get("missing") or "")),
                missing_window=_missing_window(str(gap.get("missing") or "")),
                why_it_matters=str(gap.get("why_it_matters") or ""),
                next_action=str(gap.get("next_action") or mechanism.get("cheapest_next_action") or ""),
                support_blocked_at=str(mechanism.get("current_support_ceiling") or assessment.get("support_level") or "candidate"),
                blocked_claim_decision=str(assessment.get("decision") or "unknown"),
                source_artifacts=_unique(assessment.get("source_artifacts", ())),
                refusal_reasons=_unique(assessment.get("refusal_reasons", ())),
            )
        )
    return [row for row in result if row.gap_id]


def _missing_source(missing: str) -> str:
    if "internal_json" in missing or "phase" in missing:
        return "nix_internal_json"
    if "derivation" in missing:
        return "nix_derivation_inventory"
    if "telemetry" in missing:
        return "machine_telemetry"
    if "negative_control" in missing:
        return "negative_control_check"
    if "controlled_run" in missing or "executed" in missing:
        return "controlled_benchmark_run"
    return "benchmark_manifest"


def _missing_window(missing: str) -> str:
    if "executed" in missing or "controlled_run" in missing:
        return "planned_run_window"
    if "derivation" in missing:
        return "pre_run_workload_binding"
    if "telemetry" in missing:
        return "run_overlap_window"
    if "negative_control" in missing:
        return "matched_design_window"
    return "pre_analysis_window"


def _unique(values: Iterable[Any]) -> tuple[str, ...]:
    result = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


__all__ = [
    "MachineInstrumentationGapAnalysis",
    "MachineInstrumentationGapRecord",
    "analyze_machine_instrumentation_gaps",
    "write_machine_instrumentation_gaps",
]
