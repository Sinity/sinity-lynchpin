from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .config import get_config


@dataclass
class SpotifyStream:
    end_time: Optional[datetime]
    artist: str
    track: str
    ms_played: int
    platform: Optional[str]
    context: Optional[str]
    source_file: str


def iter_streams() -> Iterator[SpotifyStream]:
    cfg = get_config()
    root = cfg.spotify_root
    if not root.exists():
        return iter(())

    files: List[Path] = []
    account_dir = root / "Spotify Account Data"
    if account_dir.exists():
        files.extend(sorted(account_dir.glob("StreamingHistory*.json")))
    extended_dir = root / "Spotify Extended Streaming History"
    if extended_dir.exists():
        files.extend(sorted(extended_dir.glob("Streaming_History*.json")))

    def generator() -> Iterator[SpotifyStream]:
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, list):
                continue
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                yield SpotifyStream(
                    end_time=_parse_time(entry),
                    artist=_extract_artist(entry),
                    track=_extract_track(entry),
                    ms_played=int(entry.get("msPlayed") or entry.get("ms_played") or 0),
                    platform=entry.get("platform"),
                    context=entry.get("reason_start") or entry.get("reason_end") or entry.get("offline"),
                    source_file=str(path),
                )

    return generator()


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
