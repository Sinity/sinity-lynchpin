"""Canonical derived personal daily signal products."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from .activity_content import (
    ActivityContentDay,
    ActivityTitleUsage,
    activity_content_daily_path,
    activity_content_manifest_path,
    activity_title_usage_path,
    iter_activity_content_days,
    iter_activity_title_usage,
)

__all__ = [
    "ActivityContentDay",
    "ActivityTitleUsage",
    "PersonalDailySignal",
    "SpotifyDailySignal",
    "activity_content_daily_path",
    "activity_content_manifest_path",
    "activity_title_usage_path",
    "personal_daily_signals_manifest_path",
    "personal_daily_signals_path",
    "spotify_daily_manifest_path",
    "spotify_daily_path",
    "iter_activity_content_days",
    "iter_activity_title_usage",
    "iter_personal_daily_signals",
    "iter_spotify_daily_signals",
]


@dataclass(frozen=True)
class PersonalDailySignal:
    source: str
    date: date
    metric: str
    value: float
    dimensions: dict[str, Any]


@dataclass(frozen=True)
class SpotifyDailySignal:
    date: date
    track_count: int
    minutes_played: float
    unique_artists: int
    unique_tracks: int
    top_artists: tuple[str, ...]
    top_tracks: tuple[str, ...]


def personal_daily_signals_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "personal/daily_signals.ndjson"


def personal_daily_signals_manifest_path(root: Path | None = None) -> Path:
    return personal_daily_signals_path(root).with_suffix(".manifest.json")


def spotify_daily_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "spotify/daily.ndjson"


def spotify_daily_manifest_path(root: Path | None = None) -> Path:
    return spotify_daily_path(root).with_suffix(".manifest.json")


def iter_personal_daily_signals(path: Path | None = None) -> Iterator[PersonalDailySignal]:
    target = path or personal_daily_signals_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical personal daily-signal materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            dimensions = payload.get("dimensions")
            yield PersonalDailySignal(
                source=str(payload.get("source") or ""),
                date=date.fromisoformat(str(payload["date"])),
                metric=str(payload.get("metric") or ""),
                value=float(payload.get("value") or 0.0),
                dimensions=dimensions if isinstance(dimensions, dict) else {},
            )


def iter_spotify_daily_signals(path: Path | None = None) -> Iterator[SpotifyDailySignal]:
    target = path or spotify_daily_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical Spotify daily materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            yield SpotifyDailySignal(
                date=date.fromisoformat(str(payload["date"])),
                track_count=int(payload.get("track_count") or 0),
                minutes_played=float(payload.get("minutes_played") or 0.0),
                unique_artists=int(payload.get("unique_artists") or 0),
                unique_tracks=int(payload.get("unique_tracks") or 0),
                top_artists=tuple(str(item) for item in payload.get("top_artists") or ()),
                top_tracks=tuple(str(item) for item in payload.get("top_tracks") or ()),
            )
