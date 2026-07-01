"""FastMCP server for the Lynchpin analysis surface.

Exposes read-only tools over the DuckDB substrate, generated analysis products,
source capability metadata, code snapshots, GitHub context, personal signals,
and local machine telemetry.

Entry: ``python -m lynchpin.mcp`` (stdio transport).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from lynchpin.mcp.registry import PUBLIC_TOOL_NAMES

app = FastMCP(
    "lynchpin",
    instructions=(
        "Lynchpin is a personal data analysis hub. This MCP server exposes "
        "read-only tools over the DuckDB substrate, evidence graph, generated "
        "analysis artifacts, code snapshots, GitHub lifecycle context, personal "
        "signals, and local machine telemetry. Start with mcp_guide for routing "
        "and mcp_capability_matrix for per-source coverage/materialization "
        "details. Use aggregate tools before query_substrate; direct SQL access "
        "is SELECT-only with read_only=True enforced at the DuckDB level."
    ),
)

_original_tool = app.tool


def _public_tool_only(*args, **kwargs):
    """Register only the collapsed public MCP tools.

    Legacy modules still contain ``@app.tool()`` decorators because their
    functions remain useful as Python implementation targets. This gate makes
    those imports inert for FastMCP registration while allowing the eight
    public routers to register normally.
    """

    decorator = _original_tool(*args, **kwargs)

    def _decorator(fn):
        if getattr(fn, "__name__", "") in PUBLIC_TOOL_NAMES:
            return decorator(fn)
        return fn

    return _decorator


app.tool = _public_tool_only  # type: ignore[method-assign]

# The collapsed public module registers the only exported MCP tools. Legacy
# domain modules are imported lazily by the routers and remain private.
from lynchpin.mcp.tools import public as _public  # noqa: E402, F401

__all__ = ["app"]
