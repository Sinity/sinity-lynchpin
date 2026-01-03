from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config


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
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[AsciinemaMetadata]:
        for path in scan_root.rglob("*.cast"):
            if not path.is_file():
                continue
            meta = _parse_cast(path)
            if meta:
                yield meta

    return generator()


def iter_audio_recordings(root: Path | None = None) -> Iterator[AudioMetadata]:
    """Scan for audio recordings and yield metadata."""
    cfg = get_config()
    scan_root = Path(root) if root else cfg.audio_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[AudioMetadata]:
        for ext in ("*.wav", "*.mp3", "*.flac", "*.opus", "*.m4a", "*.aac"):
            for path in scan_root.rglob(ext):
                if not path.is_file():
                    continue
                meta = _parse_audio(path)
                if meta:
                    yield meta

    return generator()


def iter_screenshots(root: Path | None = None) -> Iterator[ScreenMetadata]:
    """Scan for screenshots and yield metadata."""
    cfg = get_config()
    scan_root = Path(root) if root else cfg.screenshot_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[ScreenMetadata]:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4", "*.webm", "*.mkv"):
            for path in scan_root.rglob(ext):
                if not path.is_file():
                    continue
                meta = _parse_screen(path)
                if meta:
                    yield meta

    return generator()


def _parse_cast(path: Path) -> Optional[AsciinemaMetadata]:
    """Parse asciinema .cast header and derive metadata."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            header_line = fh.readline()
            if not header_line:
                return None
            header = json.loads(header_line)

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
    stat = path.stat()
    created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return AudioMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=created_at,
        duration_seconds=None,
        format=path.suffix.lstrip("."),
        channels=None,
        sample_rate=None,
    )


def _parse_screen(path: Path) -> Optional[ScreenMetadata]:
    """Parse screenshot/video metadata (basic fallback)."""
    stat = path.stat()
    created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return ScreenMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=created_at,
        width=None,
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
