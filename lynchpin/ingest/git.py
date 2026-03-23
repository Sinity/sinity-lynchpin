"""Pre-compute git commit trajectory signals for the fast-path artefact cache."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from ..sources.indices.gitstats import active_repo_paths, iter_numstat
from ..trajectory.signal_sources import _numstat_record_to_signal

app = typer.Typer(help="Git commit signal pre-computation")

_DEFAULT_OUTPUT = Path("artefacts/ingest/git/git_signals.jsonl")
_INGEST_START = datetime(2022, 1, 1, tzinfo=timezone.utc)


@app.command("signals")
def signals(
    output: Path = typer.Option(_DEFAULT_OUTPUT, "--output", help="Output JSONL path"),
) -> None:
    """Pre-compute trajectory signals for all git commits and write to JSONL.

    Run periodically (e.g. via `just ingest-git`) to keep the artefact fresh
    so that trajectory signal loading uses the fast path instead of spawning
    git subprocesses on every run.
    """
    end = datetime.now(timezone.utc)
    repos = active_repo_paths()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for record in iter_numstat(repos, since=_INGEST_START, until=end):
            sig = _numstat_record_to_signal(record)
            if sig is not None:
                fh.write(json.dumps(sig.to_dict(), ensure_ascii=False) + "\n")
                count += 1
    typer.secho(f"✓ Wrote {count} git commit signals → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
