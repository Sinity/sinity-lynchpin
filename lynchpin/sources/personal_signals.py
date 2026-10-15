"""Canonical derived personal daily signal products."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator, TextIO

from ..core.config import get_config
from ..core.errors import MaterializationError, SourceUnavailableError
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
    "spotify_daily_window",
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


def iter_personal_daily_signals(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[PersonalDailySignal]:
    target = path or personal_daily_signals_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("personal_daily_signals", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical personal daily-signal materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    handle = _open_window(target, start=start, end=end)
    with handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            row_date = date.fromisoformat(str(payload["date"]))
            if start is not None and row_date < start:
                continue
            if end is not None and row_date >= end:
                break
            dimensions = payload.get("dimensions")
            yield PersonalDailySignal(
                source=str(payload.get("source") or ""),
                date=row_date,
                metric=str(payload.get("metric") or ""),
                value=float(payload.get("value") or 0.0),
                dimensions=dimensions if isinstance(dimensions, dict) else {},
            )


def spotify_daily_window(*, start: date, end: date) -> tuple[date, date] | None:
    """Intersect a half-open request with the current export's known bounds.

    This limits demand, not validation: consumers must still require the daily
    product to cover every date in the returned window before reading it.
    """
    from ..core.input_snapshot import current_input_snapshot, read_input_path
    from ..materialization import audit_dataset

    if end <= start:
        raise ValueError("Spotify daily window end must be after start")
    exported = audit_dataset("spotify")
    snapshot = current_input_snapshot()
    if snapshot is not None and exported is not None:
        # Freeze the small bounds manifest, not the full streaming history.
        snapshot.capture(tuple(
            path for path in getattr(exported, "materialized_paths", ())
            if path.name.endswith(".manifest.json")
        ))
        exported = audit_dataset("spotify")
    if exported is not None and exported.status == "missing":
        retained_artifact = False
        for path in getattr(exported, "materialized_paths", ()):
            try:
                read_input_path(path).stat()
            except FileNotFoundError:
                continue
            retained_artifact = True
            break
        if not retained_artifact:
            raise SourceUnavailableError("spotify", reason="no Spotify export product is available")
    if exported is None or exported.status != "ready" or exported.first_date is None or exported.last_date is None:
        raise MaterializationError("spotify_daily", reason="current export bounds are unavailable or invalid")
    clipped_start = max(start, exported.first_date)
    clipped_end = min(end, exported.last_date + timedelta(days=1))
    return (clipped_start, clipped_end) if clipped_start < clipped_end else None


def iter_spotify_daily_signals(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[SpotifyDailySignal]:
    target = path or spotify_daily_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("spotify_daily", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical Spotify daily materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    handle = _open_window(target, start=start, end=end)
    with handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            row_date = date.fromisoformat(str(payload["date"]))
            if start is not None and row_date < start:
                continue
            if end is not None and row_date >= end:
                break
            yield SpotifyDailySignal(
                date=row_date,
                track_count=int(payload.get("track_count") or 0),
                minutes_played=float(payload.get("minutes_played") or 0.0),
                unique_artists=int(payload.get("unique_artists") or 0),
                unique_tracks=int(payload.get("unique_tracks") or 0),
                top_artists=tuple(str(item) for item in payload.get("top_artists") or ()),
                top_tracks=tuple(str(item) for item in payload.get("top_tracks") or ()),
            )


def _open_window(
    path: Path,
    *,
    start: date | None,
    end: date | None,
)-> TextIO:
    """Open a daily product at its indexed tail when the manifest permits it."""
    manifest_path = path.with_suffix(".manifest.json")
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    carrier = path
    if isinstance(manifest, dict) and "carrier_file" in manifest:
        carrier_name = manifest.get("carrier_file")
        if not isinstance(carrier_name, str) or not carrier_name or Path(carrier_name).name != carrier_name:
            raise SourceUnavailableError(
                "personal_daily_signals",
                path=str(manifest_path),
                reason="manifest declares an invalid carrier generation",
            )
        candidate = path.with_name(carrier_name)
        if not candidate.is_file() or candidate.is_symlink():
            raise SourceUnavailableError(
                "personal_daily_signals",
                path=str(candidate),
                reason="manifest-declared carrier generation is missing or is not a regular file",
            )
        carrier = candidate
    try:
        handle = carrier.open(encoding="utf-8")
    except OSError as exc:
        raise SourceUnavailableError(
            "personal_daily_signals",
            path=str(carrier),
            reason="selected carrier generation is unreadable",
        ) from exc
    if start is None:
        return handle
    offset = _indexed_offset(manifest.get("row_offsets"), start=start) if isinstance(manifest, dict) else None
    if offset is None:
        return handle
    handle.seek(offset)
    return handle


def _indexed_offset(offsets: object, *, start: date) -> int | None:
    if not isinstance(offsets, dict):
        return None
    candidates: list[tuple[date, int]] = []
    for raw_day, raw_offset in offsets.items():
        if not isinstance(raw_offset, int):
            continue
        try:
            day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if day <= start:
            candidates.append((day, raw_offset))
    if candidates:
        return max(candidates)[1]
    future: list[tuple[date, int]] = []
    for raw_day, raw_offset in offsets.items():
        if not isinstance(raw_offset, int):
            continue
        try:
            day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if day > start:
            future.append((day, raw_offset))
    return min(future)[1] if future else None
