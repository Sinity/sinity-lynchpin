"""Analysis artifact claim extraction registry."""

from __future__ import annotations

from typing import Any

from .analysis_artifact_code_claims import (
    _active_code_inventory_claims,
    _active_python_complexity_claims,
    _active_python_import_graph_claims,
    _active_rust_graph_claims,
)
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim, ClaimExtractor
from .analysis_artifact_quality_claims import (
    _active_ci_health_claims,
    _active_symbol_changes_claims,
    _active_symbol_diffs_claims,
    _active_symbol_index_claims,
)
from .analysis_artifact_work_claims import (
    _active_github_frontier_claims,
    _active_project_snapshot_claims,
    _active_work_package_claims,
    _project_velocity_window_claims,
)


def claims_for_artifact(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    extractor = _CLAIM_EXTRACTORS.get(artifact.name)
    if extractor is None:
        return ()
    return extractor(artifact, payload, selected=selected)


def _machine_attribution_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    claims = []
    rows = payload.get("claims")
    if not isinstance(rows, list):
        return ()
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project:
            project = "sinity-lynchpin"
        if selected and project not in selected:
            continue
        confidence = row.get("confidence")
        claims.append(
            AnalysisClaim(
                id=str(row.get("claim_id") or f"machine-attribution:{len(claims)}"),
                artifact_name=artifact.name,
                claim_type=str(row.get("claim_type") or "machine_attribution"),
                project=project,
                summary=str(row.get("summary") or "machine attribution claim"),
                payload=dict(row.get("payload") or {}),
                confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


_CLAIM_EXTRACTORS: dict[str, ClaimExtractor] = {
    "active_project_snapshot.json": _active_project_snapshot_claims,
    "active_work_packages.json": _active_work_package_claims,
    "project_velocity_windows.json": _project_velocity_window_claims,
    "active_github_frontier.json": _active_github_frontier_claims,
    "active_code_inventory.json": _active_code_inventory_claims,
    "active_python_complexity.json": _active_python_complexity_claims,
    "active_python_import_graph.json": _active_python_import_graph_claims,
    "active_rust_workspace_graph.json": _active_rust_graph_claims,
    "active_symbol_index.json": _active_symbol_index_claims,
    "active_symbol_changes.json": _active_symbol_changes_claims,
    "active_symbol_diffs.json": _active_symbol_diffs_claims,
    "active_ci_health.json": _active_ci_health_claims,
    "machine_attribution_claims.json": _machine_attribution_claims,
}
