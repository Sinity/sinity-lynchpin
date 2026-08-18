"""Operator-facing narrative of recent machine state (sinnix-6o2)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from ..core.serialization import jsonable


def _parse_end(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid --end timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _machine_explain_command(
    window_hours: float = typer.Option(
        24.0, "--window-hours", min=0.25,
        help="Length of the explained window, ending at --end (default: last 24h)",
    ),
    end: str | None = typer.Option(
        None, "--end",
        help="Window end as an ISO timestamp (default: now; naive values are local time)",
    ),
    telemetry_db: Path | None = typer.Option(
        None, "--db",
        help="Live machine telemetry SQLite (default: configured live database)",
    ),
    health_ledger: Path | None = typer.Option(
        None, "--health-ledger",
        help="Health-transitions ledger (default: /run/sinnix/health-transitions.jsonl)",
    ),
    json_output: bool = typer.Option(
        False, "--json/", help="Emit the full structured report instead of text"
    ),
) -> None:
    from lynchpin.analysis.machine.explain import (
        DEFAULT_HEALTH_LEDGER,
        machine_explain,
        render_machine_explain_text,
    )

    report = machine_explain(
        window_hours=window_hours,
        end=_parse_end(end),
        telemetry_db=telemetry_db,
        health_ledger=health_ledger or DEFAULT_HEALTH_LEDGER,
    )
    if json_output:
        sys.stdout.write(json.dumps(jsonable(report.to_dict()), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_machine_explain_text(report) + "\n")


_app = typer.Typer(
    help="Explain recent machine state as an operator-facing narrative",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_app.command()(_machine_explain_command)
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
