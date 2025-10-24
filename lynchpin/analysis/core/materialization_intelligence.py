"""Advisory materialization planning for analysis DAGs.

This module is deliberately separate from DAG execution.  It answers the
operator question before a DAG runs: which steps look cheap to skip, which ones
need work, and where the system lacks enough materialization evidence to decide.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from lynchpin.analysis.core.dag import DAG
from lynchpin.core.io import resolve_analysis_path

MaterializationAction = Literal["run", "skip", "inspect"]
MaterializationCost = Literal["cheap", "moderate", "heavy"]
MaterializationMode = Literal["batch", "incremental", "realtime"]


@dataclass(frozen=True)
class MaterializationStepPolicy:
    """Materialization policy for one DAG step."""

    step: str
    artifacts: tuple[str, ...] = ()
    substrate_sources: tuple[str, ...] = ()
    max_age_seconds: int | None = None
    cost: MaterializationCost = "moderate"
    mode: MaterializationMode = "batch"
    reason: str | None = None


@dataclass(frozen=True)
class MaterializationPlanRow:
    """One advisory preflight decision for a DAG step."""

    step: str
    action: MaterializationAction
    reason: str
    cost: MaterializationCost
    mode: MaterializationMode
    dependencies: tuple[str, ...]
    artifacts: tuple[str, ...]
    artifact_statuses: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action,
            "reason": self.reason,
            "cost": self.cost,
            "mode": self.mode,
            "dependencies": list(self.dependencies),
            "artifacts": list(self.artifacts),
            "artifact_statuses": list(self.artifact_statuses),
        }


def materialization_plan_for_dag(
    dag: DAG,
    *,
    policies: Mapping[str, MaterializationStepPolicy] | None = None,
    up_to: str | None = None,
    now: datetime | None = None,
    force: bool = False,
    requested_window: tuple[date, date] | None = None,
) -> list[MaterializationPlanRow]:
    """Build an advisory incremental materialization plan for the selected DAG slice."""

    selected = dag._selected_order(up_to)  # noqa: SLF001 - planner is DAG-adjacent infrastructure.
    policies = policies or {}
    now = now or datetime.now(timezone.utc)
    decisions: dict[str, MaterializationAction] = {}
    rows: list[MaterializationPlanRow] = []

    for step_name in selected:
        step = dag._steps[step_name]  # noqa: SLF001 - planner is DAG-adjacent infrastructure.
        policy = policies.get(step_name) or _default_policy(step_name)
        artifacts = tuple(str(_artifact_path(name)) for name in policy.artifacts)
        statuses = tuple(_artifact_status(Path(path), now=now) for path in artifacts)
        substrate_statuses = tuple(_substrate_source_status(source, now=now) for source in policy.substrate_sources)
        runnable_dependencies = tuple(dep for dep in step.depends_on if decisions.get(dep) == "run")
        artifacts_cover_window = (
            requested_window is not None
            and bool(statuses)
            and all(_artifact_covers_window(status, requested_window) for status in statuses)
        )
        substrate_sources_cover_window = (
            requested_window is not None
            and bool(substrate_statuses)
            and all(_substrate_source_covers_window(status, requested_window) for status in substrate_statuses)
        )

        if force:
            action: MaterializationAction = "run"
            reason = "forced materialization"
        elif runnable_dependencies:
            action = "run"
            reason = f"dependency scheduled: {', '.join(runnable_dependencies)}"
        elif policy.substrate_sources and (missing_substrate := [
            status["source"] for status in substrate_statuses if status["state"] == "missing"
        ]):
            action = "run"
            reason = f"missing substrate source status: {', '.join(missing_substrate)}"
        elif policy.substrate_sources and (bad_substrate := [
            status["source"] for status in substrate_statuses if status.get("status") not in {"ok", "empty"}
        ]):
            action = "run"
            reason = f"non-ready substrate source status: {', '.join(bad_substrate)}"
        elif (
            policy.substrate_sources
            and policy.max_age_seconds is not None
            and (
                expired_substrate := [
                    status["source"]
                    for status in substrate_statuses
                    if isinstance(status.get("age_seconds"), int)
                    and status["age_seconds"] > policy.max_age_seconds
                ]
            )
        ):
            if substrate_sources_cover_window:
                action = "skip"
                reason = policy.reason or "substrate source statuses cover requested window"
            else:
                action = "run"
                reason = f"substrate source status outside materialization age horizon: {', '.join(expired_substrate)}"
        elif not policy.artifacts and not policy.substrate_sources:
            action = "inspect"
            reason = policy.reason or "no materialization signal is defined"
        elif missing := [status["path"] for status in statuses if status["state"] == "missing"]:
            action = "run"
            reason = f"missing artifact: {', '.join(missing)}"
        elif policy.max_age_seconds is None:
            action = "inspect"
            reason = policy.reason or _no_age_horizon_reason(statuses)
        elif artifacts_cover_window:
            action = "skip"
            reason = policy.reason or "artifacts cover requested window"
        elif expired := [
            status["path"]
            for status in statuses
            if isinstance(status.get("age_seconds"), int)
            and status["age_seconds"] > policy.max_age_seconds
        ]:
            action = "run"
            reason = f"artifact outside materialization age horizon: {', '.join(expired)}"
        else:
            action = "skip"
            if policy.substrate_sources and not policy.artifacts:
                reason = policy.reason or "substrate source statuses are inside materialization age horizon"
            else:
                reason = policy.reason or "artifacts are inside materialization age horizon"

        decisions[step_name] = action
        rows.append(
            MaterializationPlanRow(
                step=step_name,
                action=action,
                reason=reason,
                cost=policy.cost,
                mode=policy.mode,
                dependencies=tuple(step.depends_on),
                artifacts=artifacts,
                artifact_statuses=(*statuses, *substrate_statuses),
            )
        )

    return rows


def _render_plan(rows: list[MaterializationPlanRow], *, heading: str) -> str:
    """Render a compact operator-facing DAG execution plan."""

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.action] = counts.get(row.action, 0) + 1
    summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    lines = [f"{heading}: {len(rows)} steps ({summary})"]
    for row in rows:
        labels = f"{row.cost}/{row.mode}"
        lines.append(f"  - {row.action:7} {row.step} [{labels}] {row.reason}")
    return "\n".join(lines)


def render_materialization_plan(rows: list[MaterializationPlanRow]) -> str:
    """Render a compact operator-facing materialization plan."""

    return _render_plan(rows, heading="Materialization plan")


def executable_steps(rows: list[MaterializationPlanRow]) -> set[str]:
    """Return steps that should run from a materialization plan."""

    return {row.step for row in rows if row.action == "run"}


def analysis_materialization_policies(step_names: tuple[str, ...]) -> dict[str, MaterializationStepPolicy]:
    """Return advisory artifact mappings for the broad/current-state analysis DAGs.

    These policies intentionally do not define age horizons.  Full analysis DAG
    execution is still explicit; dry-run explanation should name the real
    products without pretending arbitrary analysis steps are incrementally
    skippable.
    """

    output_map = {
        "sinex_structure": ("sinex_structure_metrics.json",),
        "sinex_temporal": ("sinex_temporal_metrics.json",),
        "active_git_facts": ("active_commit_facts.json", "active_file_change_facts.json"),
        "comparison": ("ecosystem_comparison.json",),
        "ecosystem_dashboard": ("ecosystem_dashboard.json", "ecosystem_dashboard.html"),
        "snapshot": ("analysis_snapshot.json",),
        "cross_project": ("cross_project_metrics.json",),
        "project_maps": ("module_map.json", "hotspot_map.json", "maps/project-maps.md"),
        "dependency_map": ("dependency_map.json", "maps/dependency-map.md"),
        "change_surface": ("change_surface_map.json", "maps/change-surface-map.md"),
        "machine_analysis_substrate_promote": (),
        "current_state_substrate_promote": (),
        "substrate_promote": (),
    }
    reason_map = {
        "machine_analysis_substrate_promote": "substrate promotion updates DuckDB status tables",
        "current_state_substrate_promote": "substrate promotion updates DuckDB status tables",
        "substrate_promote": "substrate promotion updates DuckDB status tables",
    }
    policies = {
        step: MaterializationStepPolicy(
            step=step,
            artifacts=output_map[step],
            max_age_seconds=None,
            reason=reason_map.get(step),
        )
        for step in step_names
        if step in output_map
    }
    if "current_state_substrate_promote" in policies:
        policies["current_state_substrate_promote"] = MaterializationStepPolicy(
            step="current_state_substrate_promote",
            artifacts=(),
            substrate_sources=(
                "commits",
                "file_changes",
                "symbols",
                "ai_work_events",
                "evidence_graph",
                "pr_review",
                "work_observations",
            ),
            max_age_seconds=5 * 60,
            cost="moderate",
            mode="incremental",
        )
    return policies


def machine_materialization_policies(step_names: tuple[str, ...]) -> dict[str, MaterializationStepPolicy]:
    """Return default advisory policies for machine-analysis materialization steps."""

    policies: dict[str, MaterializationStepPolicy] = {}
    for step in step_names:
        policies[step] = MaterializationStepPolicy(
            step=step,
            artifacts=(f"{step}.json",),
            max_age_seconds=_machine_max_age_seconds(step),
            cost=_machine_cost(step),
            mode=_machine_mode(step),
        )
    policies["machine_analysis_substrate_promote"] = MaterializationStepPolicy(
        step="machine_analysis_substrate_promote",
        artifacts=(),
        substrate_sources=(
            "work_observations",
            "machine",
            "machine_gpu_sample",
            "machine_network_sample",
            "machine_service_state",
            "machine_experiments",
        ),
        max_age_seconds=5 * 60,
        cost="moderate",
        mode="incremental",
    )
    return policies


def _default_policy(step: str) -> MaterializationStepPolicy:
    return MaterializationStepPolicy(
        step=step,
        artifacts=(f"{step}.json",),
        max_age_seconds=None,
    )


def _artifact_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    return Path(resolve_analysis_path(name))


def _artifact_status(path: Path, *, now: datetime) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "state": "missing", "age_seconds": None}
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    status = {
        "path": str(path),
        "state": "present",
        "modified_at_utc": mtime.isoformat(),
        "age_seconds": max(0, int((now - mtime).total_seconds())),
    }
    status.update(_artifact_declared_coverage(path))
    return status


def _artifact_declared_coverage(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"coverage_state": "unreadable"}
    if not isinstance(payload, dict):
        return {"coverage_state": "unsupported"}
    try:
        start = date.fromisoformat(str(payload.get("start")))
        end = date.fromisoformat(str(payload.get("end")))
    except (TypeError, ValueError):
        return {"coverage_state": "missing"}
    return {
        "coverage_state": "declared",
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
    }


def _artifact_covers_window(
    status: dict[str, Any],
    requested_window: tuple[date, date],
) -> bool:
    if status.get("coverage_state") != "declared":
        return False
    try:
        start = date.fromisoformat(str(status.get("coverage_start")))
        end = date.fromisoformat(str(status.get("coverage_end")))
    except (TypeError, ValueError):
        return False
    requested_start, requested_end = requested_window
    return start <= requested_start and requested_end <= end


def _substrate_source_covers_window(
    status: dict[str, Any],
    requested_window: tuple[date, date],
) -> bool:
    if status.get("status") not in {"ok", "empty"}:
        return False
    try:
        start = date.fromisoformat(str(status.get("window_start")))
        end = date.fromisoformat(str(status.get("window_end")))
    except (TypeError, ValueError):
        return False
    requested_start, requested_end = requested_window
    return start <= requested_start and requested_end <= end


def _no_age_horizon_reason(statuses: tuple[dict[str, Any], ...]) -> str:
    paths = [str(status["path"]) for status in statuses if status.get("path")]
    if not paths:
        return "artifact exists but no materialization age horizon is defined"
    noun = "artifact" if len(paths) == 1 else "artifacts"
    verb = "exists" if len(paths) == 1 else "exist"
    return f"{noun} {verb} but no materialization age horizon is defined: {', '.join(paths)}"


def _substrate_source_status(source: str, *, now: datetime) -> dict[str, Any]:
    try:
        from lynchpin.substrate.connection import connect, substrate_path

        with connect(substrate_path(), read_only=True) as conn:
            row = conn.execute(
                """
                SELECT status, recorded_at, row_count, refresh_id, window_start, window_end
                FROM substrate_source_status
                WHERE source = ?
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                [source],
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - planner should degrade to run.
        return {"source": source, "state": "missing", "status": "error", "reason": str(exc)}
    if row is None:
        return {"source": source, "state": "missing", "status": None, "age_seconds": None}
    recorded_at = row[1]
    if isinstance(recorded_at, datetime):
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - recorded_at.astimezone(timezone.utc)).total_seconds()))
        recorded = recorded_at.astimezone(timezone.utc).isoformat()
    else:
        age_seconds = None
        recorded = str(recorded_at) if recorded_at is not None else None
    return {
        "source": source,
        "state": "present",
        "status": row[0],
        "recorded_at_utc": recorded,
        "age_seconds": age_seconds,
        "row_count": int(row[2] or 0),
        "refresh_id": row[3],
        "window_start": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]) if row[4] is not None else None,
        "window_end": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]) if row[5] is not None else None,
    }


def _machine_max_age_seconds(step: str) -> int:
    if step in {
        "machine_telemetry_analysis",
        "machine_episode_analysis",
        "machine_gap_summary",
        "machine_work_observations",
        "workflow_mechanics",
        "keylog_analysis",
    }:
        return 5 * 60
    if step in {
        "machine_analysis_feature_frames",
        "machine_work_state_windows",
        "command_performance_windows",
        "machine_observational_deltas",
        "machine_attribution_candidates",
    }:
        return 30 * 60
    if step.startswith("machine_benchmark") or step in {
        "machine_support_assessment",
        "machine_mechanism_hypotheses",
        "machine_instrumentation_gaps",
        "machine_measurement_system",
        "machine_attribution_claims",
        "machine_assumption_checks",
    }:
        return 6 * 60 * 60
    return 60 * 60


def _machine_cost(step: str) -> MaterializationCost:
    if step.startswith("machine_benchmark") or step in {
        "machine_comparisons",
        "machine_mining",
        "machine_matched_designs",
        "machine_negative_controls",
        "machine_support_assessment",
    }:
        return "heavy"
    if step in {
        "machine_telemetry_analysis",
        "machine_episode_analysis",
        "machine_gap_summary",
        "machine_work_observations",
        "workflow_mechanics",
        "keylog_analysis",
    }:
        return "cheap"
    return "moderate"


def _machine_mode(step: str) -> MaterializationMode:
    if step in {
        "machine_telemetry_analysis",
        "machine_episode_analysis",
        "machine_gap_summary",
        "machine_work_observations",
        "workflow_mechanics",
        "keylog_analysis",
    }:
        return "realtime"
    if step in {
        "machine_analysis_substrate_promote",
        "machine_analysis_feature_frames",
        "machine_work_state_windows",
        "command_performance_windows",
    }:
        return "incremental"
    return "batch"


__all__ = [
    "MaterializationPlanRow",
    "MaterializationStepPolicy",
    "analysis_materialization_policies",
    "executable_steps",
    "machine_materialization_policies",
    "materialization_plan_for_dag",
    "render_materialization_plan",
]
