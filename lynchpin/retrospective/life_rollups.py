#!/usr/bin/env python3
"""CLI for quarterly and annual long-range life rollups."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .life_rendering import render_life_rollups
from .life_paths import (
    LATEST_LIFE_JSON,
    LIFE_ROLLUPS_OUTPUT,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(
    life_json: Path = typer.Option(
        LATEST_LIFE_JSON,
        help="Path to the monthly life summary JSON.",
    ),
    output: Path = typer.Option(
        LIFE_ROLLUPS_OUTPUT,
        help="Where to write the generated Markdown rollups.",
    ),
    quarter_limit: int = typer.Option(8, help="How many most recent quarters to include."),
    year_limit: int = typer.Option(10, help="How many most recent years to include."),
) -> None:
    payload = json.loads(life_json.read_text(encoding="utf-8"))
    rendered = render_life_rollups(
        payload,
        source_path=str(life_json),
        quarter_limit=quarter_limit,
        year_limit=year_limit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    typer.secho(f"Wrote narrative summary → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
