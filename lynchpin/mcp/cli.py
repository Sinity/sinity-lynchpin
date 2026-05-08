"""CLI entry: ``python -m lynchpin.mcp`` runs the MCP server over stdio."""

from __future__ import annotations


def main() -> None:
    """Start the lynchpin MCP server with stdio transport."""
    try:
        from lynchpin.mcp.server import app
    except ImportError as exc:
        import sys

        print(f"MCP dependencies not installed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    app.run(transport="stdio")


if __name__ == "__main__":
    main()
