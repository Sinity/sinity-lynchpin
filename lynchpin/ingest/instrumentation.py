"""Instrumentation metadata collection.

Lightweight metadata extractors for terminal recordings, audio captures, and
screen recordings. The data access layer lives in `lynchpin.sources.captures.instrumentation`;
this module only writes JSONL artefacts on demand.

CLI Usage:
    python -m lynchpin.ingest.instrumentation asciinema --root /realm/data/captures/asciinema
    python -m lynchpin.ingest.instrumentation audio --root /realm/data/captures/audio/raw
    python -m lynchpin.ingest.instrumentation screen --root /realm/data/captures/screenshot
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from ..sources.captures.instrumentation import (
    AsciinemaMetadata,
    AudioMetadata,
    ScreenMetadata,
    iter_asciinema_recordings,
    iter_audio_recordings,
    iter_screenshots,
)

app = typer.Typer(help="Instrumentation metadata collection")


@app.command()
def asciinema(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/asciinema_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect asciinema recording metadata."""
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for meta in iter_asciinema_recordings(root):
            fh.write(json.dumps(asdict(meta), ensure_ascii=False) + "\n")
            count += 1
    typer.secho(f"✓ Wrote {count} asciinema metadata records → {output}", fg=typer.colors.GREEN)


@app.command()
def audio(
    root: Path = typer.Option(Path("/realm/data/captures/audio/raw"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/audio_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect audio recording metadata."""
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for meta in iter_audio_recordings(root):
            fh.write(json.dumps(asdict(meta), ensure_ascii=False) + "\n")
            count += 1
    typer.secho(f"✓ Wrote {count} audio metadata records → {output}", fg=typer.colors.GREEN)


@app.command()
def screen(
    root: Path = typer.Option(Path("/realm/data/captures/screenshot"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/screen_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect screenshot/screen recording metadata."""
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for meta in iter_screenshots(root):
            fh.write(json.dumps(asdict(meta), ensure_ascii=False) + "\n")
            count += 1
    typer.secho(f"✓ Wrote {count} screen metadata records → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
