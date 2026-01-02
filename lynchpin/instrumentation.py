"""Instrumentation metadata collection.

Lightweight metadata extractors for terminal recordings, audio captures, and
screen recordings. No raw binaries are touched—only headers/filenames are parsed
to emit JSONL metadata for downstream analytics and embeddings.

CLI Usage:
    python -m lynchpin.instrumentation asciinema --root /realm/data/asciinema_recording
    python -m lynchpin.instrumentation audio --root /realm/data/audio/raw
    python -m lynchpin.instrumentation screen --root /realm/data/screenshot
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import typer

from .config import get_config


@dataclass
class AsciinemaMetadata:
    """Metadata for an asciinema .cast recording."""

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


@dataclass
class AudioMetadata:
    """Metadata for an audio recording file."""

    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    duration_seconds: Optional[float]
    format: Optional[str]
    channels: Optional[int]
    sample_rate: Optional[int]


@dataclass
class ScreenMetadata:
    """Metadata for a screenshot or screen recording file."""

    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    width: Optional[int]
    height: Optional[int]
    format: Optional[str]


def iter_asciinema_recordings(root: Path | None = None) -> Iterator[AsciinemaMetadata]:
    """Scan for asciinema .cast files and yield metadata."""
    cfg = get_config()
    scan_root = root or Path("/realm/data/asciinema_recording")
    if not scan_root.exists():
        return

    for path in scan_root.rglob("*.cast"):
        if not path.is_file():
            continue
        meta = _parse_cast(path)
        if meta:
            yield meta


def iter_audio_recordings(root: Path | None = None) -> Iterator[AudioMetadata]:
    """Scan for audio recordings and yield metadata."""
    scan_root = root or Path("/realm/data/audio/raw")
    if not scan_root.exists():
        return

    # Common audio extensions
    for ext in ("*.wav", "*.mp3", "*.flac", "*.opus", "*.m4a", "*.aac"):
        for path in scan_root.rglob(ext):
            if not path.is_file():
                continue
            meta = _parse_audio(path)
            if meta:
                yield meta


def iter_screenshots(root: Path | None = None) -> Iterator[ScreenMetadata]:
    """Scan for screenshots and yield metadata."""
    scan_root = root or Path("/realm/data/screenshot")
    if not scan_root.exists():
        return

    # Common image/video extensions
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4", "*.webm", "*.mkv"):
        for path in scan_root.rglob(ext):
            if not path.is_file():
                continue
            meta = _parse_screen(path)
            if meta:
                yield meta


def _parse_cast(path: Path) -> Optional[AsciinemaMetadata]:
    """Parse asciinema .cast header and derive metadata."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            header_line = fh.readline()
            if not header_line:
                return None
            header = json.loads(header_line)

            # Scan events to find duration
            last_event_time: float = 0.0
            for line in fh:
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
                    continue
    except (OSError, json.JSONDecodeError):
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
    return AsciinemaMetadata(
        path=str(path),
        size_bytes=path.stat().st_size,
        sha256=_sha256_file(path),
        created_at=created_at,
        finished_at=finished_at,
        duration_seconds=duration,
        width=header.get("width"),
        height=header.get("height"),
        title=header.get("title"),
        shell=env.get("SHELL"),
        term=env.get("TERM"),
    )


def _parse_audio(path: Path) -> Optional[AudioMetadata]:
    """Parse audio file metadata (basic fallback without ffprobe)."""
    # Basic metadata - just file info for now
    # TODO: Use ffprobe/mutagen for deeper metadata if needed
    stat = path.stat()
    created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return AudioMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=created_at,
        duration_seconds=None,  # Would need ffprobe
        format=path.suffix.lstrip("."),
        channels=None,
        sample_rate=None,
    )


def _parse_screen(path: Path) -> Optional[ScreenMetadata]:
    """Parse screenshot/video metadata (basic fallback)."""
    # Basic metadata - just file info for now
    # TODO: Use ffprobe/PIL for deeper metadata if needed
    stat = path.stat()
    created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return ScreenMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=created_at,
        width=None,  # Would need PIL/ffprobe
        height=None,
        format=path.suffix.lstrip("."),
    )


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 hash of file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# CLI


app = typer.Typer(help="Instrumentation metadata collection")


@app.command()
def asciinema(
    root: Path = typer.Option(Path("/realm/data/asciinema_recording"), "--root"),
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
    root: Path = typer.Option(Path("/realm/data/audio/raw"), "--root"),
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
    root: Path = typer.Option(Path("/realm/data/screenshot"), "--root"),
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
