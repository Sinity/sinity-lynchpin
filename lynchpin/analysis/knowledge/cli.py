"""CLI entrypoints for knowledge ledgers and transcript summaries.

Registry-backed inputs live under the configured knowledgebase root. Generated
outputs stay under the configured knowledgebase artefact root.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ...core.config import get_config
from .ledgers import write_artefact_ledger, write_session_ledger
from .session_summaries import summarise_session_transcript


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def _parse_optional_text(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_optional_path(value: str) -> Path | None:
    stripped = value.strip()
    return Path(stripped) if stripped else None


def build_parser() -> argparse.ArgumentParser:
    cfg = get_config()
    parser = argparse.ArgumentParser(
        description="Knowledge-oriented materializers for ledgers and session summaries.",
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

    summarise = subparsers.add_parser(
        "summarise-session",
        help="Summarise a rendered assistant session transcript into JSON.",
    )
    summarise.add_argument("--input", type=Path, required=True)
    summarise.add_argument("--output", type=_parse_optional_path, default=None)
    summarise.add_argument("--model", type=_parse_optional_text, default=None)
    summarise.add_argument("--backend", type=_parse_optional_text, default=None)
    summarise.add_argument("--max-chars", type=int, default=20000)
    summarise.add_argument("--force", type=_parse_bool, default=False)

    return parser


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
        status = (
            f"Wrote {result.row_count} session rows to {result.output}"
            if result.wrote
            else f"Session ledger unchanged at {result.output}"
        )
        print(status)
        return 0

    if args.command == "artefact-index":
        result = write_artefact_ledger(
            catalog=args.catalog,
            output=args.output,
            base_dir=args.base_dir,
        )
        missing = ""
        if result.missing_artifacts:
            missing = f" (missing paths: {', '.join(result.missing_artifacts)})"
        if result.wrote:
            print(f"Wrote {result.artefact_count} artefacts -> {result.output}{missing}")
        elif result.missing_artifacts:
            print(f"Reused {result.artefact_count} artefacts -> {result.output}{missing}")
        else:
            print(f"Artefact ledger unchanged at {result.output}")
        return 0

    if args.command == "summarise-session":
        result = summarise_session_transcript(
            args.input,
            output=args.output,
            model=args.model or "",
            backend=args.backend or None,
            max_chars=args.max_chars,
            force=args.force,
            log=print,
        )
        if result.skipped:
            print(f"Summary already exists at {result.output_path}")
        else:
            print(f"Summary written to {result.output_path}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
