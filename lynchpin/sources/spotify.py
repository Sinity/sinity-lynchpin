from __future__ import annotations

import json
from datetime import timedelta, date
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config

__all__ = [
    "SpotifyStream",
    "SpotifyStreamingSummary",
    "ListeningSession",
    "DailyListening",
    "iter_streams",
    "summarize_streaming",
    "top_names",
    "listening_sessions",
    "daily_listening",
]

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


def _stream_files(root: Optional[Path] = None) -> list[Path]:
    cfg = get_config()
    resolved = root or cfg.spotify_root
    if not resolved.exists():
        return []
    files: list[Path] = []
    account_dir = resolved / "Spotify Account Data"
    if account_dir.exists():
        files.extend(sorted(account_dir.glob("StreamingHistory*.json")))
    extended_dir = resolved / "Spotify Extended Streaming History"
    if extended_dir.exists():
        files.extend(sorted(extended_dir.glob("Streaming_History*.json")))
    return files


def _stream_files_signature(root: Optional[Path] = None) -> object:
    return files_signature(_stream_files(root))


@persistent_cache("spotify_streams", depends_on=_stream_files_signature)
def _load_streams(root: Optional[Path] = None) -> list[SpotifyStream]:
    rows: list[SpotifyStream] = []
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
                    platform=_optional_str(entry.get("platform")),
                    context=_optional_str(entry.get("reason_start") or entry.get("reason_end") or entry.get("offline")),
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
    hours: dict[str, float] = defaultdict(float)
    per_month_artists: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tracks: dict[str, Counter[str]] = defaultdict(Counter)

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


def top_names(per_month_counts: dict[str, Counter[str]], month: str, *, limit: int = 3) -> list[str]:
    return [name for name, _ in per_month_counts.get(month, Counter()).most_common(limit)]


def _parse_time(entry: dict[object, object]) -> Optional[datetime]:
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


def _extract_artist(entry: dict[object, object]) -> str:
    for key in ("artistName", "master_metadata_album_artist_name", "episode_show_name"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_track(entry: dict[object, object]) -> str:
    for key in ("trackName", "master_metadata_track_name", "episode_name", "audiobook_title"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Derived analytics
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ListeningSession:
    start: datetime
    end: datetime
    duration_min: float
    stream_count: int
    top_artist: str
    top_track: str
    artists: tuple[str, ...]


@dataclass(frozen=True)
class DailyListening:
    date: date
    hours: float
    stream_count: int
    top_artist: str
    top_track: str
    unique_artists: int
    unique_tracks: int


def listening_sessions(*, gap_minutes: float = 30, root: Optional[Path] = None) -> list[ListeningSession]:
    """Group streams into listening sessions by silence gaps."""
    from ..core.primitives import group_by_gap, TopN

    streams = sorted(
        (s for s in iter_streams(root=root) if s.end_time is not None),
        key=_stream_end_time,
    )
    result: list[ListeningSession] = []
    for g in group_by_gap(
        streams,
        start_of=lambda s: _stream_end_time(s) - timedelta(milliseconds=max(s.ms_played, 1000)),
        end_of=_stream_end_time,
        max_gap=gap_minutes * 60,
    ):
        artists = TopN(1)
        tracks = TopN(1)
        for s in g.items:
            if s.artist:
                artists.add(s.artist, s.ms_played)
            if s.track:
                tracks.add(s.track, s.ms_played)
        total_ms = sum(s.ms_played for s in g.items)
        result.append(ListeningSession(
            start=g.start, end=g.end, duration_min=round(total_ms / 60_000, 1),
            stream_count=len(g.items), top_artist=artists.dominant or "",
            top_track=tracks.dominant or "",
            artists=tuple(a for a, _ in artists.items),
        ))
    return result


def daily_listening(*, start: Optional[date] = None, end: Optional[date] = None,
                    root: Optional[Path] = None) -> list[DailyListening]:
    """Daily listening aggregation: hours, top artists/tracks, unique counts."""
    from ..core.primitives import TopN

    by_day: dict[date, list[SpotifyStream]] = defaultdict(list)
    for s in iter_streams(root=root):
        if s.end_time is None:
            continue
        d = s.end_time.date()
        if start and d < start:
            continue
        if end and d > end:
            continue
        by_day[d].append(s)

    result: list[DailyListening] = []
    for d in sorted(by_day):
        streams = by_day[d]
        artists = TopN(1)
        tracks = TopN(1)
        artist_set: set[str] = set()
        track_set: set[str] = set()
        total_ms = 0
        for s in streams:
            total_ms += s.ms_played
            if s.artist:
                artists.add(s.artist, s.ms_played)
                artist_set.add(s.artist)
            if s.track:
                tracks.add(s.track, s.ms_played)
                track_set.add(s.track)
        result.append(DailyListening(
            date=d, hours=round(total_ms / 3_600_000, 2), stream_count=len(streams),
            top_artist=artists.dominant or "", top_track=tracks.dominant or "",
            unique_artists=len(artist_set), unique_tracks=len(track_set),
        ))
    return result


def _stream_end_time(stream: SpotifyStream) -> datetime:
    if stream.end_time is None:
        raise ValueError("SpotifyStream.end_time is required for session grouping")
    return stream.end_time


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
