"""Reader for the ``sinnix-capture-v1`` desktop event-lane format.

Sinnix writes several small, continuous JSON-lines lanes under
``captures/<lane>/<lane>-YYYYMMDD.jsonl`` (plus a sibling ``<lane>-index.jsonl``
navigation index and a ``.seq``/``.seq.lock`` sequence-counter pair). Every
record shares one envelope::

    {"host": ..., "lane": ..., "payload": {...}, "raw_ref": ..., "schema":
     "sinnix-capture-v1", "schema_version": 1, "seq": ..., "ts": ...}

Five lanes exist today: ``notifications`` (desktop notification bus),
``mpris`` (media-player state), ``audio-index`` (speech-segment index over the
``audio`` capture), ``audio-topology`` (PipeWire graph add/remove events), and
``screen-frames`` (per-window screen frame capture: geometry, monitor,
workspace, and focused window identity alongside each frame). This module
reads the shared envelope and exposes each lane's payload as a typed record
plus a coverage-aware daily event count — it does not interpret notification
bodies, decode audio/frame content, or resolve the PipeWire graph; that stays
downstream analysis work if it is ever needed (see
``lynchpin.analysis.terminal_reconstruction`` for the screen-frames geometry
timeline join).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.primitives import logical_date

__all__ = [
    "LaneEvent",
    "NotificationEvent",
    "MprisEvent",
    "AudioIndexEntry",
    "AudioTopologyEvent",
    "ScreenFrameEvent",
    "LANES",
    "lane_root",
    "iter_lane_events",
    "notification_events",
    "mpris_events",
    "audio_index_entries",
    "audio_topology_events",
    "screen_frame_events",
    "daily_lane_activity",
]

#: Known lane names, matching their capture directory / file-prefix.
LANES: tuple[str, ...] = ("notifications", "mpris", "audio-index", "audio-topology", "screen-frames")

_DAY_FILE_RE = re.compile(r"-(\d{8})\.jsonl\Z")


@dataclass(frozen=True)
class LaneEvent:
    lane: str
    host: str
    seq: int
    timestamp: datetime
    payload: dict
    raw_ref: str | None
    source_path: str


def lane_root(lane: str, root: Path | None = None) -> Path:
    # All four lanes read through this module (notifications, mpris,
    # audio-index, audio-topology) moved from captures/ to activity/ in the
    # 2026-08-17 subject recut.
    base = root or (get_config().data_root / "activity")
    return base / lane


def _day_files(root: Path, lane: str, start: date | None, end: date | None) -> list[Path]:
    if not root.exists():
        return []
    files = []
    for path in root.glob(f"{lane}-*.jsonl"):
        match = _DAY_FILE_RE.search(path.name)
        if match is None:
            continue
        file_date = datetime.strptime(match.group(1), "%Y%m%d").date()
        if start is not None and file_date < start:
            continue
        if end is not None and file_date > end:
            continue
        files.append((file_date, path))
    return [path for _day, path in sorted(files)]


def iter_lane_events(
    lane: str,
    root: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[LaneEvent]:
    """Yield raw envelope events for one lane, oldest first.

    ``start``/``end`` bound the calendar-day filename, not ``ts`` — this
    filters cheaply without opening files outside the requested window.
    """
    base = lane_root(lane, root)
    for path in _day_files(base, lane, start, end):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                yield LaneEvent(
                    lane=str(record.get("lane") or lane),
                    host=str(record.get("host") or ""),
                    seq=int(record.get("seq") or 0),
                    timestamp=datetime.fromtimestamp(float(record["ts"]), tz=timezone.utc),
                    payload=dict(record.get("payload") or {}),
                    raw_ref=record.get("raw_ref"),
                    source_path=str(path),
                )


@dataclass(frozen=True)
class NotificationEvent:
    timestamp: datetime
    app_name: str
    summary: str
    body: str
    urgency: int
    sender: str
    source_path: str


def notification_events(
    root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> Iterator[NotificationEvent]:
    for event in iter_lane_events("notifications", root, start=start, end=end):
        payload = event.payload
        yield NotificationEvent(
            timestamp=event.timestamp,
            app_name=str(payload.get("app_name") or ""),
            summary=str(payload.get("summary") or ""),
            body=str(payload.get("body") or ""),
            urgency=int(payload.get("urgency") or 0),
            sender=str(payload.get("sender") or ""),
            source_path=event.source_path,
        )


@dataclass(frozen=True)
class MprisEvent:
    timestamp: datetime
    player: str
    event: str
    status: str
    title: str
    artist: str | None
    album: str | None
    position_seconds: float | None
    duration_seconds: float | None
    source_path: str


def mpris_events(
    root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> Iterator[MprisEvent]:
    for event in iter_lane_events("mpris", root, start=start, end=end):
        payload = event.payload
        yield MprisEvent(
            timestamp=event.timestamp,
            player=str(payload.get("player") or ""),
            event=str(payload.get("event") or ""),
            status=str(payload.get("status") or ""),
            title=str(payload.get("title") or ""),
            artist=payload.get("artist"),
            album=payload.get("album"),
            position_seconds=payload.get("position_seconds"),
            duration_seconds=payload.get("duration_seconds"),
            source_path=event.source_path,
        )


@dataclass(frozen=True)
class AudioIndexEntry:
    timestamp: datetime
    channel: str
    kind: str
    segment: str
    segment_start: datetime | None
    has_speech: bool
    raw_ref: str | None
    source_path: str


def audio_index_entries(
    root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> Iterator[AudioIndexEntry]:
    for event in iter_lane_events("audio-index", root, start=start, end=end):
        payload = event.payload
        segment_start_raw = payload.get("segment_start")
        segment_start = (
            datetime.fromtimestamp(float(segment_start_raw), tz=timezone.utc)
            if segment_start_raw is not None
            else None
        )
        yield AudioIndexEntry(
            timestamp=event.timestamp,
            channel=str(payload.get("channel") or ""),
            kind=str(payload.get("kind") or ""),
            segment=str(payload.get("segment") or ""),
            segment_start=segment_start,
            has_speech=bool(payload.get("speech_spans")),
            raw_ref=event.raw_ref,
            source_path=event.source_path,
        )


@dataclass(frozen=True)
class AudioTopologyEvent:
    timestamp: datetime
    action: str
    kind: str
    object_id: int | None
    source_path: str


def audio_topology_events(
    root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> Iterator[AudioTopologyEvent]:
    for event in iter_lane_events("audio-topology", root, start=start, end=end):
        payload = event.payload
        raw_id = payload.get("id")
        yield AudioTopologyEvent(
            timestamp=event.timestamp,
            action=str(payload.get("action") or ""),
            kind=str(payload.get("kind") or ""),
            object_id=int(raw_id) if raw_id is not None else None,
            source_path=event.source_path,
        )


@dataclass(frozen=True)
class ScreenFrameEvent:
    timestamp: datetime
    """Grim capture instant (payload ``ts``) -- precedes ``written_at`` by the
    time it took to hash/dedup/write the frame, so it is the timestamp to
    align against other capture lanes, not the envelope's wrapper-write time.
    """
    written_at: datetime
    trigger: str
    monitor: str | None
    workspace: str | None
    window_class: str | None
    window_title: str | None
    geometry: dict
    sha256: str | None
    raw_ref: str | None
    source_path: str


def screen_frame_events(
    root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> Iterator[ScreenFrameEvent]:
    for event in iter_lane_events("screen-frames", root, start=start, end=end):
        payload = event.payload
        payload_ts = payload.get("ts")
        captured_at = (
            datetime.fromtimestamp(float(payload_ts), tz=timezone.utc)
            if payload_ts is not None
            else event.timestamp
        )
        yield ScreenFrameEvent(
            timestamp=captured_at,
            written_at=event.timestamp,
            trigger=str(payload.get("trigger") or ""),
            monitor=payload.get("monitor"),
            workspace=payload.get("workspace"),
            window_class=payload.get("window_class"),
            window_title=payload.get("window_title"),
            geometry=dict(payload.get("geometry") or {}),
            sha256=payload.get("sha256"),
            raw_ref=event.raw_ref,
            source_path=event.source_path,
        )


def daily_lane_activity(
    lane: str, root: Path | None = None, *, start: date | None = None, end: date | None = None
) -> tuple[tuple[date, int], ...]:
    """Event count per logical day (06:00 boundary) for one lane."""
    counts: Counter[date] = Counter()
    for event in iter_lane_events(lane, root, start=start, end=end):
        counts[logical_date(event.timestamp)] += 1
    return tuple(sorted(counts.items()))
