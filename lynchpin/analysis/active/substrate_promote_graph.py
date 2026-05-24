"""Evidence-graph promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_EVIDENCE_GRAPH,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_graph_source(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
    write_evidence_graph: bool,
) -> None:
    if not write_evidence_graph or not selection.includes(SOURCE_EVIDENCE_GRAPH):
        return

    try:
        from lynchpin.graph.evidence_graph import build_evidence_graph
        from lynchpin.substrate.graph import promote_evidence_graph
        from lynchpin.substrate.claims import promote_analysis_claims

        graph = build_evidence_graph(
            start=window_start,
            end=window_end,
        )
        graph_counts = promote_evidence_graph(
            conn,
            refresh_id=refresh_id,
            graph=graph,
        )
        claim_count = promote_analysis_claims(
            conn,
            refresh_id=refresh_id,
            claims=_analysis_claim_rows(graph),
        )
        counts["evidence_graph_nodes"] = graph_counts.get("nodes", 0)
        counts["evidence_graph_edges"] = graph_counts.get("edges", 0)
        counts["analysis_claims"] = claim_count
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_EVIDENCE_GRAPH,
            status="ok",
            reason=None,
            row_count=counts["evidence_graph_nodes"],
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: evidence_graph promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_EVIDENCE_GRAPH,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )


def _analysis_claim_rows(graph: Any) -> list[Any]:
    from lynchpin.graph.work_correlation import supported_work_claims, work_day_correlations
    from lynchpin.substrate.claims import AnalysisClaimRow, claim_id

    rows = work_day_correlations(
        start=graph.start,
        end=graph.end,
        graph=graph,
    )
    claims = supported_work_claims(rows, graph=graph, limit=200)
    result: list[AnalysisClaimRow] = []
    for claim in claims:
        relation_ids = tuple(_relation_id(value) for value in claim.strongest_relations)
        result.append(
            AnalysisClaimRow(
                claim_id=claim_id("supported_work", claim.date, claim.project, claim.summary),
                claim_type="supported_work",
                project=claim.project,
                date=claim.date,
                support_level=claim.support_level,
                confidence=_confidence_for_support(claim.support_level),
                score=claim.score,
                summary=claim.summary,
                source_ids=(),
                relation_ids=relation_ids,
                caveats=claim.caveats,
                payload={
                    "sources": list(claim.sources),
                    "relation_count": claim.relation_count,
                    "strongest_relations": list(claim.strongest_relations),
                },
            )
        )
    for node in graph.nodes:
        if node.kind not in {"analysis_claim", "machine_experiment_claim"}:
            continue
        payload = node.payload or {}
        confidence = payload.get("confidence")
        result.append(
            AnalysisClaimRow(
                claim_id=claim_id(node.kind, node.id),
                claim_type=str(payload.get("claim_type") or node.kind),
                project=node.project,
                date=node.date,
                support_level=str(payload.get("support_level") or ""),
                confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                score=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                summary=node.summary,
                source_ids=(node.id,),
                relation_ids=(),
                caveats=tuple(c.message for c in node.caveats),
                payload=dict(payload),
            )
        )
    return result


def _confidence_for_support(level: str) -> float:
    return {"strong": 0.85, "moderate": 0.65, "weak": 0.35}.get(level, 0.25)


def _relation_id(value: str) -> str:
    return value.split(": ", 1)[0]
