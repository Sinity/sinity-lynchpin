#!/usr/bin/env python3
"""CLI for the data-dense long-range life digest renderer."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .life_outputs import render_life_digest
from .life_paths import (
    LIFE_DIGEST_OUTPUT,
    LATEST_LIFE_JSON,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(
    life_json: Path = typer.Option(
        LATEST_LIFE_JSON,
        help="Long-range life JSON (output of python -m lynchpin.retrospective.life build).",
    ),
    start: str | None = typer.Option(None, help="Start month (YYYY-MM). Defaults to the life-json range start."),
    end: str | None = typer.Option(None, help="End month (YYYY-MM). Defaults to the life-json range end."),
    output: Path = typer.Option(LIFE_DIGEST_OUTPUT, help="Output markdown file."),
    title: str = typer.Option(
        "Month-by-month (chronological)",
        help="Top-level markdown header title.",
    ),
) -> None:
    payload = json.loads(life_json.read_text(encoding="utf-8"))
    rendered = render_life_digest(
        payload,
        start=start,
        end=end,
        title=title,
        source_path=life_json.as_posix(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    typer.secho(f"Wrote digest → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
