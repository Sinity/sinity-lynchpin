"""Per-source MCP capability matrix.

Provides ``mcp_capability_matrix`` — for every known Lynchpin source, return
its canonical product, substrate table(s), graph node kinds, MCP tools,
date coverage, materialization status, and known caveats. Call this before
designing a new analysis or when unsure whether a question can be answered
through current MCP exposure.

This is a per-source view; ``mcp_capability_map`` (in substrate.py) is a
per-tool view of analytic MCP tools and their backing contracts. They are
complementary.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app


def _materialization_status_label(status: str) -> str:
    if status == "ready":
        return "ready"
    if status in {"partial", "degraded"}:
        return "partial"
    if status in {"missing", "empty"}:
        return "missing"
    if status == "error":
        return "blocked"
    return status


@app.tool()
def mcp_capability_matrix() -> list[dict[str, Any]]:
    """Per-source capability matrix for the Lynchpin MCP surface.

    Returns one row per known source with: canonical product (raw authority
    + materialized paths), substrate table(s), graph node kinds, MCP tool
    names, date bounds, requested-window coverage semantics when available,
    materialization status (ready/partial/blocked/missing), and known caveats.

    Call this before designing a new analysis, or when an agent needs to
    decide whether to query MCP, drop to ``query_substrate``, or shell out to
    raw CLI.
    """
    from lynchpin.core.source_contracts import SOURCE_CONTRACTS
    from lynchpin.materialization import audit_materialization

    audit_by_name = {row.name: row for row in audit_materialization()}

    rows: list[dict[str, Any]] = []
    for contract in SOURCE_CONTRACTS:
        name = contract.name
        audit = audit_by_name.get(name)
        materialized_paths = (
            [str(p) for p in audit.materialized_paths] if audit else []
        )
        raw_roots = [str(p) for p in audit.raw_roots] if audit else []

        materialized_product = materialized_paths[0] if materialized_paths else None
        raw_authority = raw_roots[0] if raw_roots else contract.authority

        last_date = audit.last_date if audit else None
        first_date = audit.first_date if audit else None
        row_count = audit.row_count if audit else None
        status = audit.status if audit else "missing"

        rows.append(
            {
                "source": name,
                "raw_authority": raw_authority,
                "materialized_product": materialized_product,
                "materialized_paths": materialized_paths,
                "row_count": row_count,
                "date_bounds": {
                    "first_date": first_date.isoformat() if first_date else None,
                    "last_date": last_date.isoformat() if last_date else None,
                },
                "first_date": first_date.isoformat() if first_date else None,
                "last_date": last_date.isoformat() if last_date else None,
                "materialization_status": _materialization_status_label(status),
                "raw_status": status,
                "collection_model": contract.collection_model,
                "coverage": audit.to_json()["coverage"] if audit else None,
                "substrate_table": list(contract.substrate_tables),
                "graph_node_kinds": list(contract.graph_node_kinds),
                "mcp_tools": list(contract.mcp_tools),
                "query_surface": contract.query_surface,
                "refresh_command": contract.refresh_command,
                "substrate_daily_signal": contract.substrate_daily_signal,
                "caveats": list(contract.caveats)
                + ([audit.reason] if audit and audit.reason else []),
            }
        )
    return rows


__all__ = ["mcp_capability_matrix"]
