from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config


@dataclass
class SpotifyStream:
    end_time: Optional[datetime]
    artist: str
    track: str
    ms_played: int
    platform: Optional[str]
    context: Optional[str]
    source_file: str


@dataclass(frozen=True)
class SpotifyStreamingSummary:
    hours: dict[str, float]
    artists: dict[str, Counter[str]]
    tracks: dict[str, Counter[str]]


def _stream_files(root: Optional[Path] = None) -> List[Path]:
    cfg = get_config()
    resolved = root or cfg.spotify_root
    if not resolved.exists():
        return []
    files: List[Path] = []
    account_dir = resolved / "Spotify Account Data"
    if account_dir.exists():
        files.extend(sorted(account_dir.glob("StreamingHistory*.json")))
    extended_dir = resolved / "Spotify Extended Streaming History"
    if extended_dir.exists():
        files.extend(sorted(extended_dir.glob("Streaming_History*.json")))
    return files


@persistent_cache("spotify_streams", depends_on=lambda root=None: files_signature(_stream_files(root)))
def _load_streams(root: Optional[Path]) -> List[SpotifyStream]:
    rows: List[SpotifyStream] = []
    for path in _stream_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            rows.append(
                SpotifyStream(
                    end_time=_parse_time(entry),
                    artist=_extract_artist(entry),
                    track=_extract_track(entry),
                    ms_played=int(entry.get("msPlayed") or entry.get("ms_played") or 0),
                    platform=entry.get("platform"),
                    context=entry.get("reason_start") or entry.get("reason_end") or entry.get("offline"),
                    source_file=str(path),
                )
            )
    return rows


def iter_streams(root: Optional[Path] = None) -> Iterator[SpotifyStream]:
    yield from _load_streams(root)


def summarize_streaming(
    start_month: str,
    end_month: str,
    *,
    root: Optional[Path] = None,
) -> SpotifyStreamingSummary:
    hours: Dict[str, float] = defaultdict(float)
    per_month_artists: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tracks: Dict[str, Counter[str]] = defaultdict(Counter)

    for stream in iter_streams(root=root):
        if stream.end_time is None:
            continue
        month = f"{stream.end_time.year:04d}-{stream.end_time.month:02d}"
        if not (start_month <= month <= end_month):
            continue
        hours[month] += stream.ms_played / 3_600_000
        if stream.artist:
            per_month_artists[month][stream.artist] += stream.ms_played
        if stream.track:
            per_month_tracks[month][stream.track] += stream.ms_played

    return SpotifyStreamingSummary(
        hours=dict(hours),
        artists=dict(per_month_artists),
        tracks=dict(per_month_tracks),
    )


def top_names(per_month_counts: Dict[str, Counter[str]], month: str, *, limit: int = 3) -> list[str]:
    return [name for name, _ in per_month_counts.get(month, Counter()).most_common(limit)]


def _parse_time(entry: dict) -> Optional[datetime]:
    if "endTime" in entry:
        raw = entry["endTime"]
        if isinstance(raw, str):
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M")
            except ValueError:
                pass
    if "ts" in entry and isinstance(entry["ts"], str):
        raw = entry["ts"]
        raw = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _extract_artist(entry: dict) -> str:
    for key in ("artistName", "master_metadata_album_artist_name", "episode_show_name"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_track(entry: dict) -> str:
    for key in ("trackName", "master_metadata_track_name", "episode_name", "audiobook_title"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""
