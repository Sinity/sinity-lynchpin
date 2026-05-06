"""CLI entrypoints for knowledge ledgers.

Registry-backed inputs live under the configured knowledgebase root. Generated
outputs stay under the configured knowledgebase artefact root.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ...core.config import get_config
from .ledgers import (
    ArtefactLedgerResult,
    SessionLedgerResult,
    write_artefact_ledger,
    write_session_ledger,
)


def build_parser() -> argparse.ArgumentParser:
    cfg = get_config()
    parser = argparse.ArgumentParser(
        description="Knowledge-oriented materializers for ledgers.",
    )
    subparsers = parser.add_subparsers(dest="command")

    session_index = subparsers.add_parser(
        "session-index",
        help="Export the session ledger CSV from the curated knowledgebase registry.",
    )
    session_index.add_argument(
        "--sessions-dir",
        type=Path,
        default=cfg.session_registry_dir,
    )
    session_index.add_argument(
        "--output",
        type=Path,
        default=cfg.session_ledger_output,
    )

    artefact_index = subparsers.add_parser(
        "artefact-index",
        help="Export the artefact ledger CSV from the artefact catalog.",
    )
    artefact_index.add_argument(
        "--catalog",
        type=Path,
        default=cfg.artefact_catalog,
    )
    artefact_index.add_argument(
        "--output",
        type=Path,
        default=cfg.artefact_ledger_output,
    )
    artefact_index.add_argument(
        "--base-dir",
        type=Path,
        default=Path(".").resolve(),
    )

    return parser


def add_analysis_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cfg = get_config()
    session_index = subparsers.add_parser(
        "knowledge-session-index",
        help="Export the session ledger CSV from the curated knowledgebase registry.",
    )
    session_index.add_argument(
        "--sessions-dir",
        type=Path,
        default=cfg.session_registry_dir,
    )
    session_index.add_argument(
        "--output",
        type=Path,
        default=cfg.session_ledger_output,
    )

    artefact_index = subparsers.add_parser(
        "knowledge-artefact-index",
        help="Export the artefact ledger CSV from the artefact catalog.",
    )
    artefact_index.add_argument(
        "--catalog",
        type=Path,
        default=cfg.artefact_catalog,
    )
    artefact_index.add_argument(
        "--output",
        type=Path,
        default=cfg.artefact_ledger_output,
    )
    artefact_index.add_argument(
        "--base-dir",
        type=Path,
        default=Path(".").resolve(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "session-index":
        result = write_session_ledger(
            sessions_dir=args.sessions_dir,
            output=args.output,
        )
        _print_session_status(result)
        return 0

    if args.command == "artefact-index":
        artefact_result = write_artefact_ledger(
            catalog=args.catalog,
            output=args.output,
            base_dir=args.base_dir,
        )
        _print_artefact_status(artefact_result)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def run_analysis_command(args: argparse.Namespace) -> int | None:
    if args.command == "knowledge-session-index":
        result = write_session_ledger(
            sessions_dir=args.sessions_dir,
            output=args.output,
        )
        _print_session_status(result)
        return 0

    if args.command == "knowledge-artefact-index":
        artefact_result = write_artefact_ledger(
            catalog=args.catalog,
            output=args.output,
            base_dir=args.base_dir,
        )
        _print_artefact_status(artefact_result)
        return 0

    return None


def _print_session_status(result: SessionLedgerResult) -> None:
    status = (
        f"Wrote {result.row_count} session rows to {result.output}"
        if result.wrote
        else f"Session ledger unchanged at {result.output}"
    )
    print(status)


def _print_artefact_status(result: ArtefactLedgerResult) -> None:
    missing = ""
    if result.missing_artifacts:
        missing = f" (missing paths: {', '.join(result.missing_artifacts)})"
    if result.wrote:
        print(f"Wrote {result.artefact_count} artefacts -> {result.output}{missing}")
    elif result.missing_artifacts:
        print(f"Reused {result.artefact_count} artefacts -> {result.output}{missing}")
    else:
        print(f"Artefact ledger unchanged at {result.output}")
