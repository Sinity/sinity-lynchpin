"""Typing dynamics from raw scribe-tap keylog events (inter-key intervals).

The keylog capture (``captures/keylog/logs/YYYY-MM-DD.jsonl``) records every
key press with millisecond timestamps. Daily keypress *counts* were already a
product; this source adds the *dynamics* — how the typing happened:

  - ``median_iki_ms`` / ``p90_iki_ms``: inter-key intervals within bursts
    (gaps ≥ ``BURST_GAP_MS`` end a burst and are excluded — they are pauses,
    not typing speed).
  - ``burst_count`` / ``mean_burst_len``: burst structure (a burst = a run of
    presses with every gap < ``BURST_GAP_MS``).
  - ``typing_minutes``: summed within-burst time.

Construct validity: keycodes are NOT interpreted (no content), only timing.
All presses are operator-originated (agents do not keylog as the operator).
Modifier/repeat handling varies by app, so absolute IKI values are a relative
signal — compare across days, not against typists in the literature.

Files are calendar-named but events are bucketed by ``logical_date`` (06:00
boundary), so a request for day D reads files D-1..D+1.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from ..core.config import get_config
from ..core.primitives import logical_date

#: A gap at or above this many ms ends a typing burst (it is a pause).
BURST_GAP_MS = 2000

#: Days with fewer presses than this yield no dynamics row (metrics unstable).
MIN_DAY_PRESSES = 200


@dataclass(frozen=True)
class TypingDay:
    """Typing-dynamics summary for one logical day."""

    date: date
    press_count: int
    median_iki_ms: float
    p90_iki_ms: float
    burst_count: int
    mean_burst_len: float
    typing_minutes: float


def _logs_root() -> Path:
    return get_config().captures_root / "keylog/logs"


def _press_times_ms(path: Path) -> list[tuple[datetime, float]]:
    """(aware timestamp, epoch ms) for every press event in one log file."""
    out: list[tuple[datetime, float]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or '"press"' not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "press":
                continue
            ts_raw = row.get("ts")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            out.append((ts, ts.timestamp() * 1000.0))
    return out


def daily_dynamics(
    start: date,
    end: date,
    *,
    root: Optional[Path] = None,
) -> list[TypingDay]:
    """Typing dynamics per logical day over [start, end] (inclusive).

    Days without a log file (capture gap) are absent, never zero. Days with
    fewer than ``MIN_DAY_PRESSES`` presses are also absent — a 30-press day
    has no stable IKI distribution.
    """
    logs = root if root is not None else _logs_root()
    by_day: dict[date, list[float]] = {}
    cursor = start - timedelta(days=1)
    while cursor <= end + timedelta(days=1):
        path = logs / f"{cursor.isoformat()}.jsonl"
        if path.is_file():
            for ts, ms in _press_times_ms(path):
                d = logical_date(ts)
                if start <= d <= end:
                    by_day.setdefault(d, []).append(ms)
        cursor += timedelta(days=1)

    out: list[TypingDay] = []
    for d in sorted(by_day):
        times = sorted(by_day[d])
        if len(times) < MIN_DAY_PRESSES:
            continue
        intervals = [b - a for a, b in zip(times, times[1:])]
        within = [iv for iv in intervals if 0 <= iv < BURST_GAP_MS]
        if len(within) < MIN_DAY_PRESSES // 2:
            continue
        bursts: list[int] = []
        run = 1
        for iv in intervals:
            if iv < BURST_GAP_MS:
                run += 1
            else:
                bursts.append(run)
                run = 1
        bursts.append(run)
        q = statistics.quantiles(within, n=10, method="inclusive")
        out.append(
            TypingDay(
                date=d,
                press_count=len(times),
                median_iki_ms=round(statistics.median(within), 1),
                p90_iki_ms=round(q[8], 1),
                burst_count=len(bursts),
                mean_burst_len=round(len(times) / max(len(bursts), 1), 2),
                typing_minutes=round(sum(within) / 60_000.0, 2),
            )
        )
    return out


__all__ = [
    "BURST_GAP_MS",
    "MIN_DAY_PRESSES",
    "TypingDay",
    "daily_dynamics",
]
