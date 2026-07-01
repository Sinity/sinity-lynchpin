"""CLI entry: ``python -m lynchpin.mcp`` runs or inspects the MCP server."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def _version() -> str:
    try:
        return version("lynchpin")
    except PackageNotFoundError:
        return "0.0+local"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lynchpin.mcp",
        description="Run the Lynchpin MCP server over stdio, or inspect its registered surface.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"lynchpin-mcp {_version()}",
    )
    inspect_group = parser.add_mutually_exclusive_group()
    inspect_group.add_argument(
        "--guide",
        action="store_true",
        help="Print the collapsed MCP catalog as JSON and exit.",
    )
    inspect_group.add_argument(
        "--self-check",
        action="store_true",
        help="Print MCP registry/contract self-check JSON and exit.",
    )
    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> None:
    """Start the lynchpin MCP server with stdio transport."""
    args = _parser().parse_args(argv)
    try:
        from lynchpin.mcp.server import app
    except ImportError as exc:
        import sys

        print(f"MCP dependencies not installed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    if args.guide:
        from lynchpin.mcp.tools.public import lynchpin_catalog

        _print_json(lynchpin_catalog(include_schema=True, include_legacy_map=True))
        return
    if args.self_check:
        from lynchpin.mcp.tools.public import lynchpin_status

        _print_json(lynchpin_status(view="self_check"))
        return

    app.run(transport="stdio")


if __name__ == "__main__":
    main()
