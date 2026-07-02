"""FastMCP server for the Lynchpin analysis surface.

Exposes the collapsed public router surface over the DuckDB substrate,
generated analysis products, code snapshots, GitHub lifecycle context,
personal signals, and local machine telemetry.

Entry: ``python -m lynchpin.mcp`` (stdio transport).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

app = FastMCP(
    "lynchpin",
    instructions=(
        "Lynchpin is a personal data analysis hub. This MCP server exposes "
        "eight public router tools over the DuckDB substrate, evidence graph, "
        "generated analysis artifacts, code snapshots, GitHub lifecycle "
        "context, personal signals, and local machine telemetry. Start with "
        "lynchpin_catalog(include_schema=True) for routing, source contracts, "
        "query entities, action parameters, and examples. Use lynchpin_query "
        "for read-only DSL or SELECT-only SQL access."
    ),
)

# The collapsed public module registers the only exported MCP tools. Domain
# modules are imported lazily by the routers and remain private Python helpers.
from lynchpin.mcp.tools import public as _public  # noqa: E402, F401

__all__ = ["app"]
