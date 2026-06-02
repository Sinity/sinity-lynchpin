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
# Import order: substrate first, then domain modules.
from lynchpin.mcp.tools import substrate as _substrate  # noqa: E402, F401
from lynchpin.mcp.tools import views as _views          # noqa: E402, F401
from lynchpin.mcp.tools import velocity as _velocity    # noqa: E402, F401
from lynchpin.mcp.tools import health as _health        # noqa: E402, F401
from lynchpin.mcp.tools import machine as _machine      # noqa: E402, F401
from lynchpin.mcp.tools import signals as _signals      # noqa: E402, F401
from lynchpin.mcp.tools import change as _change        # noqa: E402, F401
from lynchpin.mcp.tools import review as _review        # noqa: E402, F401
from lynchpin.mcp.tools import personal as _personal    # noqa: E402, F401
from lynchpin.mcp.tools import capability as _capability  # noqa: E402, F401
from lynchpin.mcp.tools import runtime as _runtime      # noqa: E402, F401
from lynchpin.mcp.tools import artifacts as _artifacts  # noqa: E402, F401

__all__ = ["app"]
