"""Audit parsed source coverage for a requested analysis window."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date

import typer

from ..graph.coverage import coverage_report, render_coverage_report

app = typer.Typer(add_completion=False)


@app.command()
def main(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    report = coverage_report(start=date.fromisoformat(start), end=date.fromisoformat(end))
    if json_output:
        sys.stdout.write(json.dumps(asdict(report), default=str, indent=2, sort_keys=True) + "\n")
        return
    sys.stdout.write(render_coverage_report(report) + "\n")


if __name__ == "__main__":
    app()
