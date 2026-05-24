"""CLI entry: ``python -m lynchpin.mcp`` runs the MCP server over stdio."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    try:
        return version("lynchpin")
    except PackageNotFoundError:
        return "0.0+local"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lynchpin.mcp",
        description="Run the Lynchpin MCP server over stdio.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"lynchpin-mcp {_version()}",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Start the lynchpin MCP server with stdio transport."""
    _parser().parse_args(argv)
    try:
        from lynchpin.mcp.server import app
    except ImportError as exc:
        import sys

        print(f"MCP dependencies not installed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    app.run(transport="stdio")


if __name__ == "__main__":
    main()
