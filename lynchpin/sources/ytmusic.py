"""YouTube Music listening reconstructed from live Chrome profile history.

The operator's music player moved from Spotify (export coverage ends
2025-12-17) to YouTube Music, which has no export in the lake. Every played
track navigates ``music.youtube.com/watch?v=<id>``, and the page title carries
the track name, so the live Chrome ``History`` DB yields a play-event stream:
one visit ≈ one track playback start (autoplay advances also navigate).

COVERAGE SEMANTICS
------------------
Chrome retains roughly the trailing ~90 days of visits, so this source is a
sliding window, not an archive. Days before ``coverage()[0]`` are ABSENT, not
zero. Durable coverage accumulates via ``python -m lynchpin.ingest.webhistory``
(live-profile snapshots land in the webhistory raw inbox); a future backfill
can merge those segments — tracked as follow-up work, not silently assumed.

Titles look like ``"<track> | YouTube Music - <url>"`` (the profile appends the
URL) or plain ``"<track> - YouTube Music"``; both are normalized to the track
name. Repeated visits to the same video within ``REPLAY_WINDOW_S`` are
collapsed (page refreshes / seek reloads are not replays).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..core.primitives import logical_date
from .chrome_profile import ProfileVisit, iter_profile_visits

#: Same-video visits closer than this are one playback, not a replay.
REPLAY_WINDOW_S = 90

_WATCH_LIKE = "https://music.youtube.com/watch%"
_VIDEO_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})")


@dataclass(frozen=True)
class YtMusicDay:
    """One logical day of YouTube Music listening."""

    date: date
    play_count: int          # deduplicated playback starts
    unique_tracks: int       # distinct video ids
    top_tracks: tuple[str, ...]  # up to 5 most-played track titles


def _track_title(raw_title: str) -> str:
    title = raw_title.split(" | YouTube Music")[0]
    title = title.removesuffix(" - YouTube Music")
    return title.strip()


def _video_id(url: str) -> Optional[str]:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def iter_play_events(
    *,
    visits: Optional[list[ProfileVisit]] = None,
) -> list[tuple[ProfileVisit, str]]:
    """Deduplicated (visit, video_id) play events, ascending by time."""
    rows = (
        visits
        if visits is not None
        else list(iter_profile_visits(url_like=_WATCH_LIKE))
    )
    rows.sort(key=lambda v: v.timestamp)
    last_seen: dict[str, float] = {}
    events: list[tuple[ProfileVisit, str]] = []
    for v in rows:
        vid = _video_id(v.url)
        if vid is None:
            continue
        ts = v.timestamp.timestamp()
        prev = last_seen.get(vid)
        last_seen[vid] = ts
        if prev is not None and ts - prev < REPLAY_WINDOW_S:
            continue
        events.append((v, vid))
    return events


def daily_listening(
    start: date,
    end: date,
    *,
    visits: Optional[list[ProfileVisit]] = None,
) -> list[YtMusicDay]:
    """Per-logical-day listening summary over [start, end] (inclusive).

    Days without any play event in the window are absent from the result —
    inside the profile's retention window that is a genuine no-listening day,
    before it the day is simply unobserved (missing ≠ zero; see coverage()).
    """
    by_day: dict[date, list[tuple[ProfileVisit, str]]] = defaultdict(list)
    for v, vid in iter_play_events(visits=visits):
        d = logical_date(v.timestamp)
        if start <= d <= end:
            by_day[d].append((v, vid))

    out: list[YtMusicDay] = []
    for d in sorted(by_day):
        rows = by_day[d]
        titles = Counter(_track_title(v.title) for v, _vid in rows if v.title)
        out.append(
            YtMusicDay(
                date=d,
                play_count=len(rows),
                unique_tracks=len({vid for _v, vid in rows}),
                top_tracks=tuple(t for t, _n in titles.most_common(5)),
            )
        )
    return out


def coverage(
    *, visits: Optional[list[ProfileVisit]] = None
) -> tuple[Optional[date], Optional[date]]:
    """Observed (first, last) logical dates across play events."""
    events = iter_play_events(visits=visits)
    if not events:
        return (None, None)
    days = [logical_date(v.timestamp) for v, _vid in events]
    return (min(days), max(days))


__all__ = [
    "REPLAY_WINDOW_S",
    "YtMusicDay",
    "coverage",
    "daily_listening",
    "iter_play_events",
]
