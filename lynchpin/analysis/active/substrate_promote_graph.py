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
        from lynchpin.graph.evidence_graph import analysis_claim_rows

        claim_count = promote_analysis_claims(
            conn,
            refresh_id=refresh_id,
            claims=analysis_claim_rows(graph),
        )
        node_count = graph_counts.get("nodes", 0)
        edge_count = graph_counts.get("edges", 0)
        counts["evidence_graph_nodes"] = node_count
        counts["evidence_graph_edges"] = edge_count
        counts["analysis_claims"] = claim_count
        status = "ok" if node_count > 0 else "empty"
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_EVIDENCE_GRAPH,
            status=status,
            reason=None if node_count > 0 else "evidence graph build produced no nodes",
            row_count=node_count,
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
