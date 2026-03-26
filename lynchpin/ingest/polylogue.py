"""Pre-compute polylogue activity signals for the fast-path artefact cache."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import typer

from ..sources.exports.polylogue import iter_session_profiles
from ..signals.sources import _profile_to_signals

logger = logging.getLogger(__name__)

app = typer.Typer(help="Polylogue signal pre-computation")

_DEFAULT_OUTPUT = Path("artefacts/ingest/polylogue/polylogue_signals.jsonl")
# Pull everything since Polylogue was first in use
_INGEST_START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@app.command("signals")
def signals(
    output: Path = typer.Option(_DEFAULT_OUTPUT, "--output", help="Output JSONL path"),
) -> None:
    """Pre-compute activity signals for all Polylogue sessions and write to JSONL.

    Run this periodically (e.g. via `just ingest-polylogue`) to keep the
    artefact fresh so that activity-signal loading uses the fast path.
    """
    end = datetime.now(timezone.utc)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for profile in iter_session_profiles(start=_INGEST_START, end=end):
            for signal in _profile_to_signals(profile):
                fh.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
                count += 1
    typer.secho(f"✓ Wrote {count} polylogue signals → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
