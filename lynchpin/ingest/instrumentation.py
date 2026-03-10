"""Instrumentation metadata collection."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from ..sources.captures.instrumentation import (
    AudioMetadata,
    ScreenMetadata,
    TerminalAuditEntry,
    TerminalAuditSummary,
    TerminalSessionEvent,
    TerminalSessionMetadata,
    iter_audio_recordings,
    iter_screenshots,
    iter_terminal_audit,
    iter_terminal_session_events,
    iter_terminal_sessions,
    summarize_terminal_audit,
)

app = typer.Typer(help="Instrumentation metadata collection")


def _write_jsonl(output: Path, records) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            count += 1
    return count


def _write_json(output: Path, payload: object) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.command("terminal-sessions")
def terminal_sessions(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl"),
        "--output",
    ),
) -> None:
    """Collect terminal session metadata."""

    count = _write_jsonl(output, iter_terminal_sessions(root))
    typer.secho(f"✓ Wrote {count} terminal sessions → {output}", fg=typer.colors.GREEN)


@app.command("terminal-events")
def terminal_events(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl"),
        "--output",
    ),
) -> None:
    """Collect terminal session events."""

    count = _write_jsonl(output, iter_terminal_session_events(root))
    typer.secho(f"✓ Wrote {count} terminal events → {output}", fg=typer.colors.GREEN)


@app.command("audit-terminal")
def audit_terminal(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.json"),
        "--output",
    ),
    detail_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.jsonl"),
        "--detail-output",
    ),
) -> None:
    """Audit terminal capture corpus health."""

    entries = list(iter_terminal_audit(root))
    summary = summarize_terminal_audit(iter(entries))
    detail_output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, asdict(summary))
    with detail_output.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    typer.secho(f"✓ Wrote terminal audit summary → {output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {len(entries)} terminal audit rows → {detail_output}", fg=typer.colors.GREEN)


@app.command()
def audio(
    root: Path = typer.Option(Path("/realm/data/captures/audio/raw"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/audio_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect audio recording metadata."""

    count = _write_jsonl(output, iter_audio_recordings(root))
    typer.secho(f"✓ Wrote {count} audio metadata records → {output}", fg=typer.colors.GREEN)


@app.command()
def screen(
    root: Path = typer.Option(Path("/realm/data/captures/screenshot"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/screen_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect screenshot/screen recording metadata."""

    count = _write_jsonl(output, iter_screenshots(root))
    typer.secho(f"✓ Wrote {count} screen metadata records → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
