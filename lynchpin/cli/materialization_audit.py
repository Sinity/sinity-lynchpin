"""Audit canonical dataset materialization status."""

from __future__ import annotations

import json
import sys

import typer

from ..core.serialization import jsonable
from ..materialization import audit_materialization, render_materialization_audit


app = typer.Typer(add_completion=False)


@app.command()
def main(
    json_output: bool = typer.Option(False, "--json/", help="Render structured JSON instead of Markdown"),
    ensure_supported: bool = typer.Option(
        False,
        "--ensure-supported/",
        help="Rebuild materialized products that Lynchpin can materialize locally without credentials.",
    ),
    require_ready: bool = typer.Option(
        False,
        "--require-ready",
        "--strict",
        help="Exit non-zero if any known dataset is not fully materialized.",
    ),
) -> None:
    rows = audit_materialization(ensure_supported=ensure_supported)
    if json_output:
        sys.stdout.write(json.dumps(jsonable([row.to_json() for row in rows]), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_materialization_audit(rows) + "\n")
    if require_ready and any(row.status != "ready" for row in rows):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
