"""Matched observational designs for machine boundary candidates."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime


@dataclass(frozen=True)
class MachineBoundaryMatchedDesign:
    design_id: str
    boundary_id: str
    project: str | None
    stage_name: str | None
    boundary_at: datetime
    outcome_metric: str
    treated_before_n: int
    treated_after_n: int
    treated_delta: float | None
    control_family: str
    control_before_n: int
    control_after_n: int
    control_delta: float | None
    difference_in_differences: float | None
    placebo_delta: float | None
    balance: dict[str, Any]
    negative_control_status: str
    identification_status: str
    support_ceiling: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineMatchedDesignAnalysis:
    generated_for: dict[str, Any]
    design_count: int
    supportable_design_count: int
    designs: list[MachineBoundaryMatchedDesign]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_matched_designs(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    validation_design_path: Path | None = None,
    min_side_rows: int = 3,
    limit: int = 100,
) -> MachineMatchedDesignAnalysis:
    frame_payload = load_json_object(
        feature_frames_path or resolve_analysis_path("machine_analysis_feature_frames.json"),
        label="machine analysis feature frames",
    )
    validation_payload = load_json_object(
        validation_design_path or resolve_analysis_path("machine_validation_design.json"),
        label="machine validation design",
    )
    frame = frame_payload.get("frame") if isinstance(frame_payload.get("frame"), dict) else {}
    rows = [_row(row) for row in frame.get("rows", []) if isinstance(row, dict)]
    rows = [row for row in rows if row is not None]
    boundaries = [row for row in validation_payload.get("boundaries", []) if isinstance(row, dict)]
    designs: list[MachineBoundaryMatchedDesign] = []
    for boundary in boundaries:
        designs.extend(
            _designs_for_boundary(
                boundary,
                rows=rows,
                outcome_metric=str(frame.get("outcome_metric") or "stage.duration_s"),
                min_side_rows=min_side_rows,
            )
        )
    designs.sort(
        key=lambda row: (
            row.identification_status != "design_ready",
            -(abs(row.difference_in_differences or 0.0)),
            -min(row.treated_before_n, row.treated_after_n, row.control_before_n, row.control_after_n),
            row.design_id,
        )
    )
    if limit > 0:
        designs = designs[:limit]
    return MachineMatchedDesignAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": ["machine_analysis_feature_frames.json", "machine_validation_design.json"],
            "min_side_rows": min_side_rows,
            "limit": limit,
        },
        design_count=len(designs),
        supportable_design_count=sum(1 for row in designs if row.identification_status == "design_ready"),
        designs=designs,
        caveats=[
            "matched designs are observational diagnostics and do not execute benchmarks",
            "difference-in-differences requires parallel-trend plausibility; placebo probes are only a screen",
            "balance diagnostics are descriptive because machine telemetry covariates are sparse in the current frame",
        ],
    )


def write_machine_matched_designs(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    validation_design_path: Path | None = None,
    min_side_rows: int = 3,
    limit: int = 100,
) -> MachineMatchedDesignAnalysis:
    analysis = analyze_machine_matched_designs(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        validation_design_path=validation_design_path,
        min_side_rows=min_side_rows,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _designs_for_boundary(
    boundary: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    outcome_metric: str,
    min_side_rows: int,
) -> list[MachineBoundaryMatchedDesign]:
    dimensions = boundary.get("dimensions") if isinstance(boundary.get("dimensions"), dict) else {}
    project = str(dimensions.get("project")) if dimensions.get("project") else None
    stage_name = str(dimensions.get("stage_name")) if dimensions.get("stage_name") else None
    before_commit = str(dimensions.get("before_git_commit") or "")
    after_commit = str(dimensions.get("after_git_commit") or "")
    boundary_at = parse_datetime(str(boundary.get("boundary_at") or ""))
    if boundary_at is None:
        return []
    treated_before = [
        row for row in rows
        if row["project"] == project
        and row["stage_name"] == stage_name
        and row["git_commit"] == before_commit
        and row["started_at"] < boundary_at
    ]
    treated_after = [
        row for row in rows
        if row["project"] == project
        and row["stage_name"] == stage_name
        and row["git_commit"] == after_commit
        and row["started_at"] >= boundary_at
    ]
    treated_delta = _delta(treated_before, treated_after)
    candidates = (
        ("same_project_other_stage", lambda row: row["project"] == project and row["stage_name"] != stage_name),
        ("same_stage_other_project", lambda row: row["project"] != project and row["stage_name"] == stage_name),
    )
    designs = []
    for family, predicate in candidates:
        before = [row for row in rows if predicate(row) and row["started_at"] < boundary_at]
        after = [row for row in rows if predicate(row) and row["started_at"] >= boundary_at]
        before = _nearest(before, boundary_at, limit=max(len(treated_before), min_side_rows))
        after = _nearest(after, boundary_at, limit=max(len(treated_after), min_side_rows))
        control_delta = _delta(before, after)
        did = (
            round(treated_delta - control_delta, 6)
            if treated_delta is not None and control_delta is not None
            else None
        )
        balance = _balance(treated_before + treated_after, before + after, boundary_at=boundary_at)
        placebo = _placebo_delta(treated_before)
        status, caveats = _status(
            treated_before=treated_before,
            treated_after=treated_after,
            control_before=before,
            control_after=after,
            treated_delta=treated_delta,
            control_delta=control_delta,
            placebo_delta=placebo,
            balance=balance,
            min_side_rows=min_side_rows,
        )
        designs.append(
            MachineBoundaryMatchedDesign(
                design_id=_digest("matched-design", boundary.get("boundary_id"), family),
                boundary_id=str(boundary.get("boundary_id") or ""),
                project=project,
                stage_name=stage_name,
                boundary_at=boundary_at,
                outcome_metric=outcome_metric,
                treated_before_n=len(_values(treated_before)),
                treated_after_n=len(_values(treated_after)),
                treated_delta=treated_delta,
                control_family=family,
                control_before_n=len(_values(before)),
                control_after_n=len(_values(after)),
                control_delta=control_delta,
                difference_in_differences=did,
                placebo_delta=placebo,
                balance=balance,
                negative_control_status=_negative_control_status(control_delta, treated_delta),
                identification_status=status,
                support_ceiling="natural_experiment_design" if status == "design_ready" else "candidate",
                caveats=tuple(caveats),
            )
        )
    return designs


def _status(
    *,
    treated_before: list[dict[str, Any]],
    treated_after: list[dict[str, Any]],
    control_before: list[dict[str, Any]],
    control_after: list[dict[str, Any]],
    treated_delta: float | None,
    control_delta: float | None,
    placebo_delta: float | None,
    balance: dict[str, Any],
    min_side_rows: int,
) -> tuple[str, list[str]]:
    caveats = ["not randomized; benchmark execution is still required for controlled support"]
    side_counts = [len(_values(rows)) for rows in (treated_before, treated_after, control_before, control_after)]
    if min(side_counts) < min_side_rows:
        caveats.append("insufficient matched rows on at least one side")
    if treated_delta is None or control_delta is None:
        caveats.append("missing observed numeric outcome in treatment or control")
    if _negative_control_status(control_delta, treated_delta) == "failed":
        caveats.append("negative control moves with or exceeds the treated boundary shift")
    if balance.get("time_distance_ratio") is not None and float(balance["time_distance_ratio"]) > 3.0:
        caveats.append("control timing is much farther from boundary than treated timing")
    if placebo_delta is not None and treated_delta is not None and abs(placebo_delta) > max(abs(treated_delta) * 0.5, 1.0):
        caveats.append("pre-boundary placebo delta is large relative to treated delta")
    status = "design_ready" if len(caveats) == 1 and min(side_counts) >= min_side_rows else "insufficient_identification"
    return status, caveats


def _row(row: dict[str, Any]) -> dict[str, Any] | None:
    started_at = parse_datetime(str(row.get("outcome_window_start") or ""))
    if started_at is None:
        return None
    covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
    value = row.get("outcome_value")
    return {
        "unit_id": str(row.get("unit_id") or ""),
        "started_at": started_at,
        "project": row.get("project") or covariates.get("project"),
        "stage_name": covariates.get("stage_name"),
        "git_commit": covariates.get("git_commit"),
        "cache_condition": covariates.get("cache_condition"),
        "outcome_value": float(value) if isinstance(value, (int, float)) else None,
        "observed": row.get("censoring_status") == "observed",
    }


def _values(rows: list[dict[str, Any]]) -> list[float]:
    return [float(row["outcome_value"]) for row in rows if row.get("observed") and isinstance(row.get("outcome_value"), (int, float))]


def _delta(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> float | None:
    before_values = _values(before)
    after_values = _values(after)
    if not before_values or not after_values:
        return None
    return round(statistics.median(after_values) - statistics.median(before_values), 6)


def _nearest(rows: list[dict[str, Any]], boundary_at: datetime, *, limit: int) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda row: abs((row["started_at"] - boundary_at).total_seconds()))
    return rows[:max(limit, 0)]


def _placebo_delta(before_rows: list[dict[str, Any]]) -> float | None:
    observed = sorted((row for row in before_rows if row.get("observed")), key=lambda row: row["started_at"])
    if len(observed) < 4:
        return None
    split = len(observed) // 2
    return _delta(observed[:split], observed[split:])


def _balance(treated: list[dict[str, Any]], control: list[dict[str, Any]], *, boundary_at: datetime) -> dict[str, Any]:
    treated_dist = _median_distance(treated, boundary_at)
    control_dist = _median_distance(control, boundary_at)
    cache_tv = _categorical_tv(
        [row.get("cache_condition") for row in treated],
        [row.get("cache_condition") for row in control],
    )
    ratio = None
    if treated_dist is not None and control_dist is not None and treated_dist > 0:
        ratio = round(control_dist / treated_dist, 6)
    return {
        "treated_median_seconds_from_boundary": treated_dist,
        "control_median_seconds_from_boundary": control_dist,
        "time_distance_ratio": ratio,
        "cache_condition_total_variation": cache_tv,
        "treated_observed_n": len(_values(treated)),
        "control_observed_n": len(_values(control)),
    }


def _median_distance(rows: list[dict[str, Any]], boundary_at: datetime) -> float | None:
    if not rows:
        return None
    return round(statistics.median(abs((row["started_at"] - boundary_at).total_seconds()) for row in rows), 6)


def _categorical_tv(left: list[Any], right: list[Any]) -> float | None:
    left = [item for item in left if item is not None]
    right = [item for item in right if item is not None]
    if not left or not right:
        return None
    left_counts = Counter(left)
    right_counts = Counter(right)
    keys = set(left_counts) | set(right_counts)
    left_n = sum(left_counts.values())
    right_n = sum(right_counts.values())
    return round(0.5 * sum(abs(left_counts[key] / left_n - right_counts[key] / right_n) for key in keys), 6)


def _negative_control_status(control_delta: float | None, treated_delta: float | None) -> str:
    if control_delta is None or treated_delta is None:
        return "unavailable"
    if abs(control_delta) > max(abs(treated_delta) * 0.75, 1.0):
        return "failed"
    return "passed"


def _digest(prefix: str, *parts: Any) -> str:
    raw = "\0".join(str(part) for part in parts)
    return f"machine-{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineBoundaryMatchedDesign",
    "MachineMatchedDesignAnalysis",
    "analyze_machine_matched_designs",
    "write_machine_matched_designs",
]
