"""Analysis-artifact source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from ..sources.analysis_artifacts import AnalysisArtifact, analysis_claims, latest_artifacts
from .evidence_projects import include_project


def add_analysis_artifacts(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    end: date,
    selected: set[str],
    exclude_names: frozenset[str],
) -> tuple[AnalysisArtifact, ...]:
    projects = selected or None
    artifacts = tuple(
        artifact
        for artifact in latest_artifacts(projects=projects)
        if artifact.name not in exclude_names
    )
    by_name = {artifact.name: artifact for artifact in artifacts}
    for artifact in artifacts:
        generated_at = (
            artifact.generated_at.isoformat()
            if artifact.generated_at is not None
            else None
        )
        for project in artifact.projects:
            if not include_project(project, selected):
                continue
            node_id = f"analysis:{artifact.name}:{project}"
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="analysis_artifact",
                    source="analysis",
                    date=end,
                    project=project,
                    summary=f"{artifact.name} ({artifact.kind}, {artifact.size_bytes} bytes)",
                    payload={
                        "name": artifact.name,
                        "kind": artifact.kind,
                        "projects": artifact.projects,
                        "size_bytes": artifact.size_bytes,
                        "modified_at": artifact.modified_at.isoformat(),
                        "generated_at": generated_at,
                        "top_level_keys": artifact.top_level_keys,
                        "brief": artifact.brief,
                        "references": artifact.references,
                    },
                    provenance=EvidenceProvenance(
                        "analysis", "materialized", path=str(artifact.path)
                    ),
                )
            )
            for reference in artifact.references:
                referenced = by_name.get(reference)
                if referenced is None:
                    continue
                reference_projects = referenced.projects or (project,)
                for reference_project in reference_projects:
                    if (
                        project != reference_project
                        and project not in referenced.projects
                    ):
                        continue
                    if not include_project(reference_project, selected):
                        continue
                    edges.append(
                        EvidenceEdge(
                            node_id,
                            f"analysis:{reference}:{reference_project}",
                            "references",
                            f"analysis artifact references {reference}",
                            0.8,
                        )
                    )
    return artifacts


def add_analysis_claims(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    end: date,
    selected: set[str],
    exclude_names: frozenset[str],
    artifacts: tuple[AnalysisArtifact, ...] | None = None,
) -> None:
    projects = selected or None
    for claim in analysis_claims(
        projects=projects,
        exclude_names=exclude_names,
        artifacts=artifacts,
    ):
        if not include_project(claim.project, selected):
            continue
        node_id = f"analysis-claim:{claim.id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="analysis_claim",
                source="analysis",
                date=end,
                project=claim.project,
                summary=claim.summary,
                payload={
                    "claim_type": claim.claim_type,
                    "artifact_name": claim.artifact_name,
                    "confidence": claim.confidence,
                    "generated_at": claim.generated_at.isoformat()
                    if claim.generated_at is not None
                    else None,
                    **claim.payload,
                },
                provenance=EvidenceProvenance(
                    "analysis", "materialized", path=claim.artifact_name
                ),
            )
        )
        artifact_node_id = f"analysis:{claim.artifact_name}:{claim.project}"
        edges.append(
            EvidenceEdge(
                node_id,
                artifact_node_id,
                "references",
                f"analysis claim extracted from {claim.artifact_name}",
                claim.confidence,
            )
        )
