"""Cross-source presence model.

A single source of truth for "was the operator present and active during
hour H" — combining AW AFK status (which we know is broken for 2026),
keylog presses, AW window events, and (optionally) polylogue session
activity. This abstracts AW as one input rather than treating its
``not-afk`` status as ground truth.

Why this exists:
    AW AFK data is unreliable in 2026. Specifically:
    - Jan 2026 had 263k AFK events with 68% sub-1s duration (status flapping).
    - Feb-May 2026 has multi-hour AFK events (p50 ≈ 1-4 hours/event), which
      span actual gaps in AW uptime.
    - In a verified 3-hour window (2026-05-12 08-10 UTC), AW recorded
      ZERO events but keylog captured 7273 presses.

    Therefore lynchpin must not take ``not-afk`` as proof of presence and
    ``afk`` as proof of absence. Both can be wrong; both can be missing.

Returned per-hour record:
    HourPresence(
        hour_utc=datetime,
        keylog_presses=int,        # 0 if no keylog records (could be no
                                   # data OR no typing)
        aw_window_events=int,
        aw_nonafk_sec=float,
        aw_afk_sec=float,
        aw_data_present=bool,      # any AW event of any kind this hour
        keylog_data_present=bool,  # keylog file exists for this day
        derived_state=str,         # see _classify()
        confidence=str,            # 'high' / 'medium' / 'low'
    )

The ``derived_state`` is one of:
    "active_typing"     — keylog>50 presses (~baseline of 1/min over an hour)
    "active_no_typing"  — aw_window_events>10 OR aw_nonafk_sec>1800 but
                          keylog<10. Reading / watching / agentic work.
    "afk_confirmed"     — aw_afk_sec dominant AND keylog<5
    "data_gap"          — neither AW nor keylog present
    "ai_session_driven" — caller may compute this with polylogue context

Confidence:
    high   — multiple sources agree
    medium — one source clear, others silent
    low    — sources disagree (e.g., aw=afk but keylog=high)
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

KEYLOG_DIR = Path("/realm/data/captures/keylog/logs")


@dataclass(frozen=True)
class HourPresence:
    hour_utc: datetime
    keylog_presses: int
    aw_window_events: int
    aw_nonafk_sec: float
    aw_afk_sec: float
    aw_n_afk_events: int
    aw_data_present: bool
    keylog_data_present: bool
    derived_state: str
    confidence: str


# Thresholds chosen empirically; tweak as needed.
_TYPING_PRESSES_PER_HOUR = 50    # ~1/min sustained → operator typing
_LIGHT_PRESSES_PER_HOUR  = 5     # < this and we treat as silent kb
_BUSY_WINDOW_EVENTS      = 10
_LONG_NONAFK_SEC         = 1800  # half-hour
_FLAPPING_AFK_EVENTS     = 100   # >100/hour = watcher broken


def _classify(presses: int, window_events: int, nonafk_sec: float,
              afk_sec: float, aw_events: int, kl_data: bool, aw_data: bool,
              afk_flapping: bool) -> tuple[str, str]:
    """Return (derived_state, confidence)."""
    if not aw_data and not kl_data:
        return ("data_gap", "high")
    if not aw_data and kl_data:
        if presses >= _TYPING_PRESSES_PER_HOUR:
            return ("active_typing", "medium")  # aw down, kb says active
        elif presses >= _LIGHT_PRESSES_PER_HOUR:
            return ("active_typing", "low")     # marginal
        else:
            return ("data_gap", "high")
    # AW data exists
    if presses >= _TYPING_PRESSES_PER_HOUR:
        return ("active_typing", "high")
    # AFK watcher flapping → don't trust afk_sec
    if afk_flapping:
        if window_events >= _BUSY_WINDOW_EVENTS or presses >= _LIGHT_PRESSES_PER_HOUR:
            return ("active_no_typing", "low")
        return ("data_gap", "low")  # AFK broken AND no other signal
    # Trust afk durations
    if afk_sec > 2 * nonafk_sec and afk_sec > 1500 and presses < _LIGHT_PRESSES_PER_HOUR:
        return ("afk_confirmed", "high")
    if nonafk_sec > _LONG_NONAFK_SEC and presses < _LIGHT_PRESSES_PER_HOUR \
       and window_events < 5:
        # AW says present but no other evidence
        return ("active_no_typing", "low")
    if nonafk_sec > _LONG_NONAFK_SEC:
        return ("active_no_typing", "medium")
    if window_events >= _BUSY_WINDOW_EVENTS:
        return ("active_no_typing", "medium")
    return ("data_gap", "medium")


def hourly_presence(start: date, end: date) -> Iterator[HourPresence]:
    """Yield one HourPresence per UTC hour in the inclusive [start, end] range.

    Streams the canonical AW NDJSON once and the relevant keylog files;
    O(events_in_window) memory and IO.
    """
    win_start = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
    win_end   = datetime.combine(end + timedelta(days=1), datetime.min.time()).replace(
        tzinfo=timezone.utc)

    # Buckets
    nonafk = defaultdict(float)
    afk = defaultdict(float)
    afk_events = defaultdict(int)
    win_events = defaultdict(int)
    presses = defaultdict(int)
    aw_data_hours: set[datetime] = set()
    kl_data_dates: set[date] = set()

    cfg = get_config()
    aw_ndjson = cfg.captures_root / "activitywatch/events.ndjson"
    if not aw_ndjson.exists():
        return

    def _hr(dt: datetime) -> datetime:
        return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

    with aw_ndjson.open() as f:
        for line in f:
            try:
                p = json.loads(line)
            except Exception:
                continue
            bucket = p.get("bucket", "")
            start_iso = p.get("start", "")
            end_iso = p.get("end", "")
            try:
                s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            except Exception:
                continue
            if e < win_start or s > win_end:
                continue

            aw_data_hours.add(_hr(s))

            if bucket.startswith("aw-watcher-afk_"):
                status = (p.get("data") or {}).get("status") or ""
                status = status.strip().lower()
                if e <= s:
                    afk_events[_hr(s)] += 1
                    continue
                cur = s
                while cur < e:
                    next_hr = cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                    chunk_end = min(e, next_hr)
                    dur = (chunk_end - cur).total_seconds()
                    k = _hr(cur)
                    afk_events[k] += 1
                    if status in {"afk", "away"}:
                        afk[k] += dur
                    elif status in {"not-afk", "active", "present"}:
                        nonafk[k] += dur
                    cur = chunk_end
            elif bucket.startswith("aw-watcher-window_"):
                win_events[_hr(s)] += 1

    # Keylog
    for f in sorted(KEYLOG_DIR.glob("*.jsonl")) if KEYLOG_DIR.exists() else []:
        try:
            d = date.fromisoformat(f.name[:10])
        except ValueError:
            continue
        if d < start or d > end:
            continue
        kl_data_dates.add(d)
        with f.open() as fh:
            for line in fh:
                try:
                    p = json.loads(line)
                except Exception:
                    continue
                if p.get("event") != "press":
                    continue
                try:
                    s = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
                except Exception:
                    continue
                if s < win_start or s > win_end:
                    continue
                presses[_hr(s)] += 1

    # Emit per-hour
    h = win_start
    while h < win_end:
        ev = afk_events.get(h, 0)
        flapping = ev > _FLAPPING_AFK_EVENTS
        kl_data = h.date() in kl_data_dates
        aw_data = h in aw_data_hours
        state, conf = _classify(
            presses.get(h, 0),
            win_events.get(h, 0),
            nonafk.get(h, 0.0),
            afk.get(h, 0.0),
            ev, kl_data, aw_data, flapping,
        )
        yield HourPresence(
            hour_utc=h,
            keylog_presses=presses.get(h, 0),
            aw_window_events=win_events.get(h, 0),
            aw_nonafk_sec=round(nonafk.get(h, 0.0), 1),
            aw_afk_sec=round(afk.get(h, 0.0), 1),
            aw_n_afk_events=ev,
            aw_data_present=aw_data,
            keylog_data_present=kl_data,
            derived_state=state,
            confidence=conf,
        )
        h += timedelta(hours=1)


__all__ = ["HourPresence", "hourly_presence"]
