"""Explicit, private-file entrypoint for input reconstruction."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

import typer

from .input_activity import ActivityPolicy, ContextSpan, aware, load_foreground_contexts, reconstruct_activity
from lynchpin.sources.keylog import read_input_trace


def register_commands(app: typer.Typer) -> None:
    @app.command("input-activity", help="Reconstruct input evidence into a new private JSON file (no materialization)")
    def input_activity(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        out: Path = typer.Option(..., "--out"),
        logs_root: Path | None = typer.Option(None, "--logs-root"),
        contexts: Path | None = typer.Option(None, "--contexts", help="JSON list; otherwise read raw foreground events"),
        aw_db: Path | None = typer.Option(None, "--aw-db"),
        bucket: str | None = typer.Option(None, "--bucket"),
        anchors: Path | None = typer.Option(None, "--anchors"),
        reported_periods: Path | None = typer.Option(None, "--reported-periods"),
        include_text: bool = typer.Option(False, "--include-text"),
        bout_gap: float = typer.Option(30.0, "--bout-gap"),
        reading_gap: float = typer.Option(300.0, "--reading-gap"),
    ) -> None:
        try:
            lo, hi = aware(datetime.fromisoformat(start)), aware(datetime.fromisoformat(end))
            if hi <= lo:
                raise ValueError("end must follow start")
            policy = ActivityPolicy(bout_gap_s=bout_gap, reading_gap_s=reading_gap)
            if out.exists() or out.is_symlink():
                raise ValueError("output already exists; choose a new private path")
            context_rows = [ContextSpan(
                **{**row, "start": datetime.fromisoformat(row["start"]), "end": datetime.fromisoformat(row["end"])},
            ) for row in json.loads(contexts.read_text())] if contexts else load_foreground_contexts(start=lo, end=hi, db_path=aw_db, bucket=bucket)
            result = reconstruct_activity(
                read_input_trace(start=lo, end=hi, logs_root=logs_root), context_rows, policy=policy,
                reported_periods=json.loads(reported_periods.read_text()) if reported_periods else (),
                anchors=json.loads(anchors.read_text()) if anchors else (), include_text=include_text,
            )
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo("Private input-activity evidence written.")
