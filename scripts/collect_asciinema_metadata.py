#!/usr/bin/env python3
"""
Harvest metadata for asciinema recordings without touching the raw event payload.

This is the first instrumentation harvester outlined in
`docs/plans/instrumentation-metadata.md`.  It scans a recording directory
(defaults to `/realm/data/asciinema_recording/`), parses each `.cast` file's
header, and emits lightweight JSONL metadata describing timestamps, dimensions,
shell context, approximate duration, and file hashes.  Outputs land in
`data/derived/asciinema_metadata.jsonl` by default so downstream dashboards and
embeddings can reason about coverage without reading the recordings themselves.

Example:
    python scripts/collect_asciinema_metadata.py \
        --root /realm/data/asciinema_recording \
        --output data/derived/asciinema_metadata.jsonl
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


@dataclass
class CastMetadata:
    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    finished_at: Optional[str]
    duration_seconds: Optional[float]
    width: Optional[int]
    height: Optional[int]
    title: Optional[str]
    shell: Optional[str]
    term: Optional[str]


def iter_cast_files(root: Path) -> Iterator[Path]:
    """Yield all asciinema `.cast` files under the given root."""
    if not root.exists():
        return iter(())
    return (path for path in root.rglob("*.cast") if path.is_file())


def parse_cast(path: Path) -> Optional[CastMetadata]:
    """
    Parse the header line of an asciinema recording and derive basic metadata.

    The first line is JSON with fields such as `width`, `height`, `timestamp`,
    `title`, `env`.  Subsequent lines are JSON arrays `[time, type, data]`.  We
    scan the file once to identify the final event timestamp so we can infer the
    duration and finish time without storing the full payload.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            header_line = handle.readline()
            if not header_line:
                typer.secho(f"Skipping empty cast: {path}", err=True, fg=typer.colors.YELLOW)
                return None
            header = json.loads(header_line)

            last_event_time: float = 0.0
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if isinstance(event, list) and event:
                        time_offset = float(event[0])
                        if time_offset > last_event_time:
                            last_event_time = time_offset
                except json.JSONDecodeError:
                    # Ignore malformed lines but keep scanning.
                    continue
    except (OSError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to process {path}: {exc}", err=True, fg=typer.colors.RED)
        return None

    start_ts = header.get("timestamp")
    created_at = (
        datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
        if isinstance(start_ts, (int, float))
        else None
    )
    duration = last_event_time if last_event_time > 0 else None
    finished_at = (
        datetime.fromtimestamp(start_ts + last_event_time, tz=timezone.utc).isoformat()
        if created_at and duration is not None and isinstance(start_ts, (int, float))
        else None
    )

    env = header.get("env") or {}
    metadata = CastMetadata(
        path=str(path),
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        created_at=created_at,
        finished_at=finished_at,
        duration_seconds=duration,
        width=header.get("width"),
        height=header.get("height"),
        title=header.get("title"),
        shell=env.get("SHELL"),
        term=env.get("TERM"),
    )
    return metadata


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA-256 hash of a file without loading it entirely into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(records: Iterable[CastMetadata], output_path: Path) -> int:
    """Write cast metadata records as JSON lines. Returns number of records written."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            if record is None:
                continue
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            count += 1
    return count


@app.command()
def harvest(
    root: Path = typer.Option(
        Path("/realm/data/asciinema_recording"),
        "--root",
        help="Directory containing asciinema `.cast` recordings",
    ),
    output: Path = typer.Option(
        Path("data/derived/asciinema_metadata.jsonl"),
        "--output",
        help="Where to write JSONL metadata",
    ),
) -> None:
    """Harvest asciinema metadata and emit JSON lines for downstream analytics."""
    if not root.exists():
        typer.secho(f"Root path does not exist: {root}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Scanning {root} for asciinema recordings...")
    records = []
    for path in iter_cast_files(root):
        meta = parse_cast(path)
        if meta:
            records.append(meta)

    written = write_jsonl(records, output)
    typer.secho(f"Wrote {written} metadata rows → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
