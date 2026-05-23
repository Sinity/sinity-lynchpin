"""Build deterministic Lynchpin narrative artifacts."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Literal

import typer

ContextPackMode = Literal["local-fast", "local-heavy", "network"]


def narrate(*args: object, **kwargs: object) -> object:
    from ..graph.narrative import narrate as impl

    return impl(*args, **kwargs)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid date: {value!r}") from exc


def _narrative_command(
    start: str = typer.Option(..., "--start", help="Start logical date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End logical date (YYYY-MM-DD)"),
    mode: str = typer.Option("local-fast", "--mode", help="Context-pack cost mode"),
    project: list[str] = typer.Option(None, "--project", help="Restrict narrative to project; repeatable"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write Markdown to this path"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write structured JSON to this path"),
    moment_limit: int = typer.Option(24, "--moment-limit", min=1),
    min_score: float = typer.Option(1.5, "--min-score"),
) -> None:
    if mode not in {"local-fast", "local-heavy", "network"}:
        raise typer.BadParameter(
            f"argument --mode: invalid choice: {mode!r} (choose from 'local-fast', 'local-heavy', 'network')"
        )
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        raise typer.BadParameter("--end must be on or after --start")
    report = narrate(
        start=start_d,
        end=end_d,
        projects=list(project or []),
        mode=mode,  # type: ignore[arg-type]
        moment_limit=moment_limit,
        min_score=min_score,
        out=str(output) if output else None,
        json_out=str(json_output) if json_output else None,
    )
    if output is None:
        from ..graph.narrative import render_narrative_markdown

        sys.stdout.write(render_narrative_markdown(report) + "\n")


_app = typer.Typer(
    help="Build deterministic narrative artifacts",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_app.command()(_narrative_command)
_command = typer.main.get_command(_app)


def main(argv: list[str] | None = None) -> int:
    import click

    try:
        _command.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.UsageError as exc:
        sys.stderr.write(f"Error: {exc.format_message()}\n")
        return 2
    except (typer.Exit, SystemExit) as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            code = getattr(exc, "code", 0)
        return int(code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
