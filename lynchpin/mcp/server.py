"""FastMCP server for the lynchpin DuckDB substrate.

Exposes read-only access to commit facts, evidence graphs, project-day
correlations, closure chains, overlap edges, and PR review rows.

Entry: ``python -m lynchpin.mcp`` (stdio transport).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

app = FastMCP(
    "lynchpin",
    instructions=(
        "Lynchpin is a personal data analysis hub. This MCP server exposes "
        "the DuckDB substrate — commit facts, AI work events, evidence graphs, "
        "project-day correlations, closure chains, overlap edges, and PR review "
        "rows — as read-only tools for agent-driven analysis. "
        "All SQL access is SELECT-only with read_only=True enforced at the "
        "DuckDB level. Use query_substrate for ad-hoc exploration and the "
        "typed tools for structured access."
    ),
)

# Tools register themselves via @app.tool() decorators in submodules.
# Import order matters: substrate first (defines query_substrate used by views).
from lynchpin.mcp.tools import substrate as _substrate  # noqa: E402, F401
from lynchpin.mcp.tools import views as _views  # noqa: E402, F401

__all__ = ["app"]
