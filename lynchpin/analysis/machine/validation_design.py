"""Discovery/validation splits and boundary inventory for machine mining."""

from __future__ import annotations

from collections import defaultdict
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
class MachineDiscoveryValidationSplit:
    split_id: str
    unit_type: str
    split_policy: str
    discovery_window_start: datetime | None
    discovery_window_end: datetime | None
    validation_window_start: datetime | None
    validation_window_end: datetime | None
    discovery_row_count: int
    validation_row_count: int
    leakage_status: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineBoundaryCandidate:
    boundary_id: str
    boundary_type: str
    boundary_at: datetime
    dimensions: dict[str, Any]
    before_row_count: int
    after_row_count: int
    before_median: float | None
    after_median: float | None
    median_delta: float | None
    candidate_controls: tuple[str, ...]
    support_ceiling: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineValidationDesignAnalysis:
    generated_for: dict[str, Any]
    split: MachineDiscoveryValidationSplit
    boundary_count: int
    boundaries: list[MachineBoundaryCandidate]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_validation_design(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    split_fraction: float = 0.70,
    min_boundary_rows: int = 3,
    limit: int = 100,
) -> MachineValidationDesignAnalysis:
    payload = load_json_object(
        feature_frames_path or resolve_analysis_path("machine_analysis_feature_frames.json"),
        label="machine analysis feature frames",
    )
    frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
    rows = [_frame_row(row) for row in frame.get("rows", []) if isinstance(row, dict)]
    rows = [row for row in rows if row is not None]
    rows.sort(key=lambda row: (row["started_at"], row["unit_id"]))
    split = _split(rows, unit_type=str(frame.get("unit_type") or "unknown"), split_fraction=split_fraction)
    boundaries = _git_revision_boundaries(rows, min_rows=min_boundary_rows)
    boundaries.sort(key=lambda row: (abs(row.median_delta or 0.0) * -1.0, -row.after_row_count, row.boundary_id))
    if limit > 0:
        boundaries = boundaries[:limit]
    caveats = [
        "validation split is temporal and deterministic; it is not randomized",
        "boundary candidates are natural-experiment opportunities only, not claims",
    ]
    if split.validation_row_count == 0:
        caveats.append("no validation rows available after split")
    return MachineValidationDesignAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_analysis_feature_frames.json",
            "split_fraction": split_fraction,
            "min_boundary_rows": min_boundary_rows,
            "limit": limit,
        },
        split=split,
        boundary_count=len(boundaries),
        boundaries=boundaries,
        caveats=caveats,
    )


def write_machine_validation_design(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    split_fraction: float = 0.70,
    min_boundary_rows: int = 3,
    limit: int = 100,
) -> MachineValidationDesignAnalysis:
    analysis = analyze_machine_validation_design(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        split_fraction=split_fraction,
        min_boundary_rows=min_boundary_rows,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _split(rows: list[dict[str, Any]], *, unit_type: str, split_fraction: float) -> MachineDiscoveryValidationSplit:
    if not rows:
        return MachineDiscoveryValidationSplit(
            split_id=_digest("split", unit_type, "empty"),
            unit_type=unit_type,
            split_policy=f"temporal_first_{split_fraction:.2f}_discovery_rest_validation",
            discovery_window_start=None,
            discovery_window_end=None,
            validation_window_start=None,
            validation_window_end=None,
            discovery_row_count=0,
            validation_row_count=0,
            leakage_status="empty",
            caveats=("no feature-frame rows available",),
        )
    split_idx = min(len(rows), max(1, int(len(rows) * max(0.0, min(1.0, split_fraction)))))
    discovery = rows[:split_idx]
    validation = rows[split_idx:]
    return MachineDiscoveryValidationSplit(
        split_id=_digest("split", unit_type, rows[0]["started_at"], rows[-1]["started_at"], len(rows), split_idx),
        unit_type=unit_type,
        split_policy=f"temporal_first_{split_fraction:.2f}_discovery_rest_validation",
        discovery_window_start=discovery[0]["started_at"],
        discovery_window_end=discovery[-1]["started_at"],
        validation_window_start=validation[0]["started_at"] if validation else None,
        validation_window_end=validation[-1]["started_at"] if validation else None,
        discovery_row_count=len(discovery),
        validation_row_count=len(validation),
        leakage_status="ok" if not validation or discovery[-1]["started_at"] <= validation[0]["started_at"] else "invalid",
        caveats=("validation window is empty" if not validation else "split is temporal, not randomized",),
    )


def _git_revision_boundaries(rows: list[dict[str, Any]], *, min_rows: int) -> list[MachineBoundaryCandidate]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("project"), row.get("stage_name"))].append(row)
    result: list[MachineBoundaryCandidate] = []
    for (project, stage_name), items in grouped.items():
        items.sort(key=lambda row: (row["started_at"], row["unit_id"]))
        commit_runs: list[tuple[str, list[dict[str, Any]]]] = []
        for row in items:
            commit = str(row.get("git_commit") or "")
            if not commit:
                continue
            if not commit_runs or commit_runs[-1][0] != commit:
                commit_runs.append((commit, [row]))
            else:
                commit_runs[-1][1].append(row)
        for idx in range(1, len(commit_runs)):
            before_commit, before = commit_runs[idx - 1]
            after_commit, after = commit_runs[idx]
            before_values = _observed_values(before)
            after_values = _observed_values(after)
            if len(before_values) < min_rows or len(after_values) < min_rows:
                continue
            before_median = statistics.median(before_values)
            after_median = statistics.median(after_values)
            boundary_at = after[0]["started_at"]
            result.append(
                MachineBoundaryCandidate(
                    boundary_id=_digest("boundary", "git_commit", project, stage_name, before_commit, after_commit, boundary_at),
                    boundary_type="git_commit_transition",
                    boundary_at=boundary_at,
                    dimensions={
                        "project": project,
                        "stage_name": stage_name,
                        "before_git_commit": before_commit,
                        "after_git_commit": after_commit,
                    },
                    before_row_count=len(before_values),
                    after_row_count=len(after_values),
                    before_median=round(before_median, 6),
                    after_median=round(after_median, 6),
                    median_delta=round(after_median - before_median, 6),
                    candidate_controls=("same_project_other_stage", "same_stage_other_project", "pre_boundary_placebo"),
                    support_ceiling="natural_experiment",
                    caveats=(
                        "git transition is observational and non-randomized",
                        "requires matched controls and cache/workload checks before any natural-experiment claim",
                    ),
                )
            )
    return result


def _observed_values(rows: list[dict[str, Any]]) -> list[float]:
    values = []
    for row in rows:
        if row.get("censoring_status") != "observed":
            continue
        value = row.get("outcome_value")
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _frame_row(row: dict[str, Any]) -> dict[str, Any] | None:
    started_at = parse_datetime(str(row.get("outcome_window_start") or ""))
    if started_at is None:
        return None
    covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
    return {
        "unit_id": str(row.get("unit_id") or ""),
        "started_at": started_at,
        "project": row.get("project") or covariates.get("project"),
        "stage_name": covariates.get("stage_name"),
        "git_commit": covariates.get("git_commit"),
        "outcome_value": row.get("outcome_value"),
        "censoring_status": row.get("censoring_status"),
    }


def _digest(prefix: str, *parts: Any) -> str:
    raw = "\0".join(str(part) for part in parts)
    return f"machine-{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineBoundaryCandidate",
    "MachineDiscoveryValidationSplit",
    "MachineValidationDesignAnalysis",
    "analyze_machine_validation_design",
    "write_machine_validation_design",
]
