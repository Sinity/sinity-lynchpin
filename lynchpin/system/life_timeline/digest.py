#!/usr/bin/env python3
"""Thin CLI wrapper for the data-dense life timeline digest renderer."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from lynchpin import retrospective
from .paths import (
    LATEST_LIFE_TIMELINE_JSON,
    LIFE_TIMELINE_DIGEST_OUTPUT,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(
    life_json: Path = typer.Option(
        LATEST_LIFE_TIMELINE_JSON,
        help="Life timeline JSON (output of python -m lynchpin.system.life_timeline).",
    ),
    start: str | None = typer.Option(None, help="Start month (YYYY-MM). Defaults to the life-json range start."),
    end: str | None = typer.Option(None, help="End month (YYYY-MM). Defaults to the life-json range end."),
    output: Path = typer.Option(LIFE_TIMELINE_DIGEST_OUTPUT, help="Output markdown file."),
    title: str = typer.Option(
        "Month-by-month (chronological)",
        help="Top-level markdown header title.",
    ),
) -> None:
    payload = json.loads(life_json.read_text(encoding="utf-8"))
    rendered = retrospective.render_life_timeline_digest(
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
