"""Negative-control and placebo checks for machine attribution designs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineNegativeControl:
    control_id: str
    design_id: str
    boundary_id: str
    project: str | None
    stage_name: str | None
    control_kind: str
    support_required: bool
    expected_null_rationale: str
    primary_delta: float | None
    control_delta: float | None
    placebo_delta: float | None
    status: str
    interpretation: str
    support_consequence: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineNegativeControlAnalysis:
    generated_for: dict[str, Any]
    control_count: int
    by_status: dict[str, int]
    controls: list[MachineNegativeControl]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_negative_controls(
    *,
    start: date | None = None,
    end: date | None = None,
    matched_designs_path: Path | None = None,
    limit: int = 200,
) -> MachineNegativeControlAnalysis:
    payload = load_json_object(
        matched_designs_path or resolve_analysis_path("machine_matched_designs.json"),
        label="machine matched designs",
    )
    controls = [
        control
        for row in payload.get("designs", [])
        if isinstance(row, dict)
        for control in _controls_for_design(row)
    ]
    controls.sort(key=lambda row: (row.status != "failed", row.boundary_id, row.control_kind, row.control_id))
    if limit > 0:
        controls = controls[:limit]
    by_status: dict[str, int] = {}
    for control in controls:
        by_status[control.status] = by_status.get(control.status, 0) + 1
    return MachineNegativeControlAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_matched_designs.json",
            "limit": limit,
        },
        control_count=len(controls),
        by_status=dict(sorted(by_status.items())),
        controls=controls,
        caveats=[
            "negative controls are observational checks, not randomized evidence",
            "failed controls cap natural-experiment support unless later sensitivity analysis resolves them",
        ],
    )


def write_machine_negative_controls(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    matched_designs_path: Path | None = None,
    limit: int = 200,
) -> MachineNegativeControlAnalysis:
    analysis = analyze_machine_negative_controls(
        start=start,
        end=end,
        matched_designs_path=matched_designs_path,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _controls_for_design(row: dict[str, Any]) -> tuple[MachineNegativeControl, ...]:
    design_id = str(row.get("design_id") or "")
    if not design_id:
        return ()
    project = str(row.get("project")) if row.get("project") else None
    stage_name = str(row.get("stage_name")) if row.get("stage_name") else None
    primary = _number(row.get("treated_delta"))
    control = _number(row.get("control_delta"))
    placebo = _number(row.get("placebo_delta"))
    return (
        _control(
            design_id=design_id,
            boundary_id=str(row.get("boundary_id") or ""),
            project=project,
            stage_name=stage_name,
            kind=str(row.get("control_family") or "matched_control"),
            rationale="unaffected matched workload should not move with the treated boundary",
            primary_delta=primary,
            control_delta=control,
            placebo_delta=None,
            status=str(row.get("negative_control_status") or "unavailable"),
            caveats=tuple(str(c) for c in row.get("caveats", ()) if c),
        ),
        _control(
            design_id=design_id,
            boundary_id=str(row.get("boundary_id") or ""),
            project=project,
            stage_name=stage_name,
            kind="pre_boundary_placebo",
            rationale="pre-boundary split should not reproduce the post-boundary treated shift",
            primary_delta=primary,
            control_delta=None,
            placebo_delta=placebo,
            status=_placebo_status(primary, placebo),
            caveats=tuple(str(c) for c in row.get("caveats", ()) if c),
        ),
    )


def _control(
    *,
    design_id: str,
    boundary_id: str,
    project: str | None,
    stage_name: str | None,
    kind: str,
    rationale: str,
    primary_delta: float | None,
    control_delta: float | None,
    placebo_delta: float | None,
    status: str,
    caveats: tuple[str, ...],
) -> MachineNegativeControl:
    return MachineNegativeControl(
        control_id=f"machine-negative-control:{hashlib.sha1((design_id + kind).encode()).hexdigest()[:16]}",
        design_id=design_id,
        boundary_id=boundary_id,
        project=project,
        stage_name=stage_name,
        control_kind=kind,
        support_required=kind != "pre_boundary_placebo",
        expected_null_rationale=rationale,
        primary_delta=primary_delta,
        control_delta=control_delta,
        placebo_delta=placebo_delta,
        status=status,
        interpretation=_interpretation(status, kind),
        support_consequence=_support_consequence(status),
        caveats=caveats,
    )


def _placebo_status(primary: float | None, placebo: float | None) -> str:
    if primary is None or placebo is None:
        return "unavailable"
    if abs(placebo) > max(abs(primary) * 0.5, 1.0):
        return "failed"
    return "passed"


def _interpretation(status: str, kind: str) -> str:
    if status == "passed":
        return f"{kind} did not reproduce the treated effect"
    if status == "failed":
        return f"{kind} moved enough to threaten identification"
    return f"{kind} could not be evaluated with current rows"


def _support_consequence(status: str) -> str:
    if status == "passed":
        return "does not block natural-experiment design by itself"
    if status == "failed":
        return "blocks natural-experiment support until sensitivity analysis resolves it"
    return "leaves support capped at candidate"


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "MachineNegativeControl",
    "MachineNegativeControlAnalysis",
    "analyze_machine_negative_controls",
    "write_machine_negative_controls",
]
