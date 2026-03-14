"""Instrumentation metadata collection."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from ..sources.captures.instrumentation import (
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


def _write_audit_bundle(
    root: Path,
    *,
    sessions_output: Path,
    events_output: Path,
    audit_output: Path,
    audit_detail_output: Path,
    quarantine_output: Path,
) -> tuple[int, int, int, int]:
    session_count = _write_jsonl(sessions_output, iter_terminal_sessions(root))
    event_count = _write_jsonl(events_output, iter_terminal_session_events(root))
    entries = list(iter_terminal_audit(root))
    summary = summarize_terminal_audit(iter(entries))
    _write_json(audit_output, asdict(summary))
    audit_detail_output.parent.mkdir(parents=True, exist_ok=True)
    quarantine_output.parent.mkdir(parents=True, exist_ok=True)
    with audit_detail_output.open("w", encoding="utf-8") as audit_fh:
        with quarantine_output.open("w", encoding="utf-8") as quarantine_fh:
            quarantine_count = 0
            for entry in entries:
                payload = json.dumps(asdict(entry), ensure_ascii=False)
                audit_fh.write(payload + "\n")
                if entry.status == "damaged":
                    quarantine_fh.write(payload + "\n")
                    quarantine_count += 1
    return session_count, event_count, len(entries), quarantine_count


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
    quarantine_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_quarantine.jsonl"),
        "--quarantine-output",
    ),
) -> None:
    """Audit terminal capture corpus health."""

    entries = list(iter_terminal_audit(root))
    summary = summarize_terminal_audit(iter(entries))
    detail_output.parent.mkdir(parents=True, exist_ok=True)
    quarantine_output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, asdict(summary))
    quarantine_count = 0
    with detail_output.open("w", encoding="utf-8") as detail_fh:
        with quarantine_output.open("w", encoding="utf-8") as quarantine_fh:
            for entry in entries:
                payload = json.dumps(asdict(entry), ensure_ascii=False)
                detail_fh.write(payload + "\n")
                if entry.status == "damaged":
                    quarantine_fh.write(payload + "\n")
                    quarantine_count += 1
    typer.secho(f"✓ Wrote terminal audit summary → {output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {len(entries)} terminal audit rows → {detail_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {quarantine_count} quarantine candidates → {quarantine_output}", fg=typer.colors.GREEN)


@app.command("terminal-metadata")
def terminal_metadata(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    sessions_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl"),
        "--sessions-output",
    ),
    events_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl"),
        "--events-output",
    ),
    audit_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.json"),
        "--audit-output",
    ),
    audit_detail_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.jsonl"),
        "--audit-detail-output",
    ),
    quarantine_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_quarantine.jsonl"),
        "--quarantine-output",
    ),
) -> None:
    """Collect terminal sessions/events and audit outputs in one command."""

    session_count, event_count, audit_count, quarantine_count = _write_audit_bundle(
        root,
        sessions_output=sessions_output,
        events_output=events_output,
        audit_output=audit_output,
        audit_detail_output=audit_detail_output,
        quarantine_output=quarantine_output,
    )
    typer.secho(f"✓ Wrote {session_count} terminal sessions → {sessions_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {event_count} terminal events → {events_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote terminal audit summary → {audit_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {audit_count} terminal audit rows → {audit_detail_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {quarantine_count} quarantine candidates → {quarantine_output}", fg=typer.colors.GREEN)


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
