from __future__ import annotations

import json
from datetime import timedelta, date, timezone
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config
from ..core.primitives import logical_date

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
    "daily_activity",
]

@dataclass(frozen=True)
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


def _stream_files(root: Optional[Path] = None, *, ensure: bool = True) -> list[Path]:
    cfg = get_config()
    if root is None:
        if ensure:
            from ..materialization import ensure_materialized

            ensure_materialized("spotify")
        canonical = cfg.exports_root / "spotify/processed/streaming_history.ndjson"
        if canonical.exists():
            return [canonical]
        raise FileNotFoundError(
            f"canonical Spotify stream materialization is missing: {canonical}. "
            "Run python -m lynchpin.ingest.exports_materialize spotify."
        )
    resolved = root
    if not resolved.exists():
        return []
    files: list[Path] = []
    account_dir = resolved / "Spotify Account Data"
    if account_dir.exists():
        files.extend(sorted(account_dir.glob("StreamingHistory*.json")))
    extended_dir = resolved / "Spotify Extended Streaming History"
    if extended_dir.exists():
        files.extend(sorted(extended_dir.glob("Streaming_History*.json")))
    files.extend(sorted(resolved.glob("StreamingHistory*.json")))
    return files


def _stream_files_signature(*args: object, **kwargs: object) -> object:
    root = kwargs.get("root")
    if root is None and args:
        root = args[0]
    root_path: Optional[Path]
    if root is None:
        root_path = None
    elif isinstance(root, Path):
        root_path = root
    else:
        root_path = Path(str(root))
    return files_signature(_stream_files(root_path, ensure=False))


@persistent_cache("spotify_streams", depends_on=_stream_files_signature)  # type: ignore[arg-type]
def _load_streams(root: Optional[Path] = None) -> list[SpotifyStream]:
    rows: list[SpotifyStream] = []
    for path in _stream_files(root, ensure=False):
        if path.suffix == ".ndjson":
            rows.extend(_read_canonical_streams(path))
            continue
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


def _read_canonical_streams(path: Path) -> list[SpotifyStream]:
    rows: list[SpotifyStream] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            rows.append(
                SpotifyStream(
                    end_time=_parse_time(payload),
                    artist=str(payload.get("artist") or ""),
                    track=str(payload.get("track") or ""),
                    ms_played=int(payload.get("ms_played") or 0),
                    platform=_optional_str(payload.get("platform")),
                    context=_optional_str(payload.get("context")),
                    source_file=str(payload.get("source_file") or path),
                )
            )
    return rows


def iter_streams(
    root: Optional[Path] = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[SpotifyStream]:
    """Iterate Spotify streams, optionally bounded by half-open logical dates."""
    if ensure and root is None:
        from ..materialization import ensure_materialized

        ensure_materialized("spotify", window=(start, end) if start and end else None)
    for stream in _load_streams(root):
        if stream.end_time is not None and (start is not None or end is not None):
            d = logical_date(stream.end_time)
            if start is not None and d < start:
                continue
            if end is not None and d >= end:
                continue
        yield stream


def summarize_streaming(
    start_month: str,
    end_month: str,
    *,
    root: Optional[Path] = None,
) -> SpotifyStreamingSummary:
    hours: dict[str, float] = defaultdict(float)
    per_month_artists: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tracks: dict[str, Counter[str]] = defaultdict(Counter)
    start, end = _month_window(start_month, end_month)

    for stream in iter_streams(root=root, start=start, end=end):
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


def _month_window(start_month: str, end_month: str) -> tuple[date, date]:
    start_year, start_month_num = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_num = (int(part) for part in end_month.split("-", 1))
    start = date(start_year, start_month_num, 1)
    if end_month_num == 12:
        end = date(end_year + 1, 1, 1)
    else:
        end = date(end_year, end_month_num + 1, 1)
    if end <= start:
        raise ValueError("end_month must be after or equal to start_month")
    return start, end


def top_names(per_month_counts: dict[str, Counter[str]], month: str, *, limit: int = 3) -> list[str]:
    return [name for name, _ in per_month_counts.get(month, Counter()).most_common(limit)]


def _parse_time(entry: dict[object, object]) -> Optional[datetime]:
    if "end_time" in entry and isinstance(entry["end_time"], str):
        raw = str(entry["end_time"]).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    if "endTime" in entry:
        raw_obj = entry["endTime"]
        if isinstance(raw_obj, str):
            try:
                return datetime.strptime(raw_obj, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
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
    # Wall-clock hours during which at least one stream was playing.
    # Capped at 24h. Computed by interval-merging stream windows so
    # concurrent playback across devices doesn't double-count.
    hours: float
    # Sum of stream durations across all devices. Can exceed 24h on days
    # with multi-device concurrent playback (operator's desktop + phone
    # both playing the same Spotify account). Useful when device count is
    # informative; not "hours of the day" in the wall-clock sense.
    playback_hours: float
    stream_count: int
    top_artist: str
    top_track: str
    unique_artists: int
    unique_tracks: int


def listening_sessions(
    *,
    gap_minutes: float = 30,
    root: Optional[Path] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    ensure: bool = True,
) -> list[ListeningSession]:
    """Group streams into listening sessions by silence gaps."""
    from ..core.primitives import group_by_gap, TopN

    streams = sorted(
        (s for s in iter_streams(root=root, start=start, end=end, ensure=ensure) if s.end_time is not None),
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


def daily_listening(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    root: Optional[Path] = None,
    ensure: bool = True,
) -> list[DailyListening]:
    """Daily listening aggregation: hours, top artists/tracks, unique counts."""
    from datetime import timedelta, timezone
    from ..core.primitives import TopN, merge_intervals
    if ensure and root is None and start is not None and end is not None:
        from ..materialization import ensure_materialized

        ensure_materialized("spotify", window=(start, end))

    def _to_utc(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    by_day: dict[date, list[SpotifyStream]] = defaultdict(list)
    for s in iter_streams(root=root, start=start, end=end, ensure=False):
        if s.end_time is None:
            continue
        d = logical_date(s.end_time)
        by_day[d].append(s)

    result: list[DailyListening] = []
    for d in sorted(by_day):
        streams = by_day[d]
        artists = TopN(1)
        tracks = TopN(1)
        artist_set: set[str] = set()
        track_set: set[str] = set()
        total_ms = 0
        intervals = []
        for s in streams:
            total_ms += s.ms_played
            end_ts = _to_utc(s.end_time) if s.end_time else None
            start_ts = end_ts - timedelta(milliseconds=s.ms_played) if end_ts else None
            if start_ts is not None and end_ts is not None:
                intervals.append((start_ts, end_ts))
            if s.artist:
                artists.add(s.artist, s.ms_played)
                artist_set.add(s.artist)
            if s.track:
                tracks.add(s.track, s.ms_played)
                track_set.add(s.track)
        merged = merge_intervals(intervals)
        wall_clock_s = sum((iv[1] - iv[0]).total_seconds() for iv in merged)
        result.append(DailyListening(
            date=d,
            hours=round(wall_clock_s / 3600, 2),
            playback_hours=round(total_ms / 3_600_000, 2),
            stream_count=len(streams),
            top_artist=artists.dominant or "", top_track=tracks.dominant or "",
            unique_artists=len(artist_set), unique_tracks=len(track_set),
        ))
    return result


daily_activity = daily_listening


def _stream_end_time(stream: SpotifyStream) -> datetime:
    if stream.end_time is None:
        raise ValueError("SpotifyStream.end_time is required for session grouping")
    return stream.end_time


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def daily_genre_minutes(
    start: date, end: date, *, cache_path: Optional[Path] = None
) -> dict[date, dict[str, float]]:
    """Per logical day, minutes of listening attributed to each artist genre.

    Genres are absent from the export, so each streamed artist's genres are
    resolved via the Spotify catalog API (``spotify_genres.artist_genres_by_name``,
    disk-cached). A stream's minutes are added to every genre of its artist; a day
    only appears if it had a covered stream (missing != zero). Requires
    SPOTIFY_CLIENT_ID/SECRET — raises SourceUnavailableError otherwise.
    """
    from collections import defaultdict

    from .spotify_genres import artist_genres_by_name

    streams = [
        s
        for s in iter_streams(start=start, end=end)
        if s.end_time is not None
        and s.artist
    ]
    names = {s.artist for s in streams}
    genres_by_name = artist_genres_by_name(names, cache_path=cache_path)

    out: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for stream in streams:
        assert stream.end_time is not None  # filtered above
        day = logical_date(stream.end_time)
        minutes = stream.ms_played / 60_000.0
        for genre in genres_by_name.get(stream.artist, []):
            out[day][genre] += minutes
    return {day: dict(genres) for day, genres in out.items()}
