"""Mechanism hypotheses for machine attribution candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineMechanismRecord:
    mechanism_id: str
    candidate_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    projects: tuple[str, ...]
    metrics: tuple[str, ...]
    suspected_factors: tuple[str, ...]
    mechanism_family: str
    expected_signatures: tuple[str, ...]
    falsifiers: tuple[str, ...]
    discriminating_measurements: tuple[str, ...]
    current_support_ceiling: str
    cheapest_next_action: str
    support_levels: tuple[str, ...]
    refusal_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MachineMechanismAnalysis:
    generated_for: dict[str, Any]
    mechanism_count: int
    mechanisms: list[MachineMechanismRecord]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_mechanisms(
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
) -> MachineMechanismAnalysis:
    payload = load_json_object(
        support_assessment_path or resolve_analysis_path("machine_support_assessment.json"),
        label="machine support assessment",
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("assessments", []):
        if not isinstance(row, dict):
            continue
        mechanism = row.get("mechanism")
        if not isinstance(mechanism, dict):
            continue
        mechanism_id = str(mechanism.get("mechanism_id") or "")
        if not mechanism_id:
            continue
        grouped.setdefault(mechanism_id, []).append(row)
    mechanisms = [_record(mechanism_id, rows) for mechanism_id, rows in sorted(grouped.items())]
    mechanisms.sort(key=lambda row: (-len(row.candidate_ids), row.mechanism_family, row.mechanism_id))
    return MachineMechanismAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_support_assessment.json",
        },
        mechanism_count=len(mechanisms),
        mechanisms=mechanisms,
        caveats=[
            "mechanism hypotheses are falsifiable explanations, not support upgrades",
            "current support ceiling is inherited from support assessments and benchmark readiness",
        ],
    )


def write_machine_mechanisms(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
) -> MachineMechanismAnalysis:
    analysis = analyze_machine_mechanisms(start=start, end=end, support_assessment_path=support_assessment_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _record(mechanism_id: str, rows: list[dict[str, Any]]) -> MachineMechanismRecord:
    mechanism = rows[0].get("mechanism") if isinstance(rows[0].get("mechanism"), dict) else {}
    return MachineMechanismRecord(
        mechanism_id=mechanism_id,
        candidate_ids=_unique(row.get("candidate_id") for row in rows),
        assessment_ids=_unique(row.get("assessment_id") for row in rows),
        projects=_unique(row.get("project") for row in rows),
        metrics=_unique(row.get("metric") for row in rows),
        suspected_factors=_unique(row.get("suspected_factor") for row in rows),
        mechanism_family=str(mechanism.get("mechanism_family") or "unknown"),
        expected_signatures=tuple(str(item) for item in mechanism.get("expected_signatures", ()) if item),
        falsifiers=tuple(str(item) for item in mechanism.get("falsifiers", ()) if item),
        discriminating_measurements=tuple(str(item) for item in mechanism.get("discriminating_measurements", ()) if item),
        current_support_ceiling=str(mechanism.get("current_support_ceiling") or "candidate"),
        cheapest_next_action=str(mechanism.get("cheapest_next_action") or ""),
        support_levels=_unique(row.get("support_level") for row in rows),
        refusal_reasons=_unique(
            reason
            for row in rows
            for reason in (row.get("refusal_reasons") if isinstance(row.get("refusal_reasons"), list) else [])
        ),
    )


def _unique(values: Any) -> tuple[str, ...]:
    result = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


__all__ = [
    "MachineMechanismAnalysis",
    "MachineMechanismRecord",
    "analyze_machine_mechanisms",
    "write_machine_mechanisms",
]
