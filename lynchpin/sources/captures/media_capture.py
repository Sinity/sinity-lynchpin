"""Audio and screen capture metadata surfaces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ...core.config import get_config


@dataclass
class AudioMetadata:
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
    path: str
    size_bytes: int
    sha256: str
    created_at: Optional[str]
    width: Optional[int]
    height: Optional[int]
    format: Optional[str]


def iter_audio_recordings(root: Path | None = None) -> Iterator[AudioMetadata]:
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


def _parse_audio(path: Path) -> Optional[AudioMetadata]:
    stat = path.stat()
    return AudioMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone().isoformat(),
        duration_seconds=None,
        format=path.suffix.lstrip("."),
        channels=None,
        sample_rate=None,
    )


def _parse_screen(path: Path) -> Optional[ScreenMetadata]:
    stat = path.stat()
    return ScreenMetadata(
        path=str(path),
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone().isoformat(),
        width=None,
        height=None,
        format=path.suffix.lstrip("."),
    )


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
