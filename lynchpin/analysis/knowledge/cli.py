"""CLI entrypoints for knowledge ledgers.

Registry-backed inputs live under the configured knowledgebase root. Generated
outputs stay under the configured knowledgebase artefact root.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ...core.config import get_config
from .ledgers import (
    ArtefactLedgerResult,
    SessionLedgerResult,
    write_artefact_ledger,
    write_session_ledger,
)


app = typer.Typer(
    help="Knowledge-oriented materializers for ledgers.",
    no_args_is_help=True,
)


@app.command("session-index", help="Export the session ledger CSV from the curated knowledgebase registry.")
def _session_index(
    sessions_dir: Path = typer.Option(None, "--sessions-dir"),
    output: Path = typer.Option(None, "--output"),
) -> None:
    cfg = get_config()
    result = write_session_ledger(
        sessions_dir=sessions_dir or cfg.session_registry_dir,
        output=output or cfg.session_ledger_output,
    )
    _print_session_status(result)


@app.command("artefact-index", help="Export the artefact ledger CSV from the artefact catalog.")
def _artefact_index(
    catalog: Path = typer.Option(None, "--catalog"),
    output: Path = typer.Option(None, "--output"),
    base_dir: Path = typer.Option(None, "--base-dir"),
) -> None:
    cfg = get_config()
    artefact_result = write_artefact_ledger(
        catalog=catalog or cfg.artefact_catalog,
        output=output or cfg.artefact_ledger_output,
        base_dir=base_dir or Path(".").resolve(),
    )
    _print_artefact_status(artefact_result)


def register_commands(parent: typer.Typer) -> None:
    cfg = get_config()

    @parent.command(
        "knowledge-session-index",
        help="Export the session ledger CSV from the curated knowledgebase registry.",
    )
    def _knowledge_session_index(
        sessions_dir: Path = typer.Option(cfg.session_registry_dir, "--sessions-dir"),
        output: Path = typer.Option(cfg.session_ledger_output, "--output"),
    ) -> None:
        result = write_session_ledger(
            sessions_dir=sessions_dir,
            output=output,
        )
        _print_session_status(result)

    @parent.command(
        "knowledge-artefact-index",
        help="Export the artefact ledger CSV from the artefact catalog.",
    )
    def _knowledge_artefact_index(
        catalog: Path = typer.Option(cfg.artefact_catalog, "--catalog"),
        output: Path = typer.Option(cfg.artefact_ledger_output, "--output"),
        base_dir: Path = typer.Option(Path(".").resolve(), "--base-dir"),
    ) -> None:
        artefact_result = write_artefact_ledger(
            catalog=catalog,
            output=output,
            base_dir=base_dir,
        )
        _print_artefact_status(artefact_result)


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
    except (typer.Exit, SystemExit) as exc:
        code = exc.exit_code if isinstance(exc, typer.Exit) else (exc.code or 0)
        return int(code or 0)
    return 0


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
