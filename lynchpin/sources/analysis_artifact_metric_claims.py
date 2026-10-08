from __future__ import annotations

from typing import Any

from ..core.projects import canonical_project_name
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim


def _cross_project_metrics_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        return ()
    claims: list[AnalysisClaim] = []
    for project_name, metrics in projects.items():
        if not isinstance(metrics, dict):
            continue
        project = canonical_project_name(project_name)
        if project is None or (selected and project not in selected):
            continue
        commit_count = int(metrics.get("commit_count") or 0)
        file_count = int(metrics.get("file_change_count") or 0)
        loc = int(metrics.get("loc") or 0)
        repo_count = int(metrics.get("repo_count") or 0)
        summary = (
            f"{project}: {commit_count} commits, {file_count} file changes, "
            f"{loc:,} LOC across {repo_count} repos"
        )
        claims.append(
            AnalysisClaim(
                id=f"cross-project-metrics:{project}",
                artifact_name=artifact.name,
                claim_type="cross_project_work_package",
                project=project,
                summary=summary,
                payload={
                    "commit_count": commit_count,
                    "file_change_count": file_count,
                    "loc": loc,
                    "repo_count": repo_count,
                },
                confidence=0.60,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)
