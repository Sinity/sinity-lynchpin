"""Cross-source operator rhythm — when deep work actually happens.

Existing tools are silo'd: ``circadian`` returns ActivityWatch hourly
focus, ``temporal_rhythm`` returns commit-hour distribution from
``commit_fact``, ``daily_rhythm_fingerprint`` groups commits into
morning/afternoon/evening buckets. None of them join across sources.

This module composes those signals into a single (hour-of-day,
day-of-week) matrix combining focus minutes, commit count, AI session
count, and machine-pressure episode count, so the question "what hours
do I do deep work?" can be answered from one query rather than four.

Construct-validity boundary:
- "focus minutes" comes from ActivityWatch; project attribution there is
  known to be partial (window-title→cwd mapping fails for many sessions).
  The hourly aggregate is more reliable than the per-project view.
- "ai_session_count" is per-JSONL session (auto-compact and resume
  inflate this); see polylogue #866 for the lineage primitive that will
  give a logical-conversation count. Reported as-is until that lands.
- "pressure_episode_count" counts distinct machine_episode rows whose
  start_ts falls in the hour; episodes spanning hours are credited to
  start hour only.
- Rows with all-zero signals are omitted; the absence of a row means
  "no observed activity", not "data missing".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class HourBucket:
    """One (day-of-week, hour-of-day) bucket of cross-source signals.

    `dow` follows ISO convention: 0=Monday … 6=Sunday.
    `hour` is 0-23 in local time of the source timestamps.
    """

    dow: int
    hour: int
    focus_min: float
    commit_count: int
    ai_session_count: int
    pressure_episode_count: int


@dataclass(frozen=True)
class OperatorRhythm:
    """Composite rhythm matrix for a window.

    `partial_sources` names sources that yielded zero rows for the
    window — callers should treat the corresponding columns as
    "unobserved" rather than "zero activity".
    """

    start: date
    end: date
    project: str | None
    buckets: tuple[HourBucket, ...]
    partial_sources: tuple[str, ...]
    peak_focus_hour: tuple[int, int] | None  # (dow, hour)
    peak_commit_hour: tuple[int, int] | None
    peak_combined_hour: tuple[int, int] | None  # by normalized combined score


def compute_operator_rhythm(
    *,
    start: date,
    end: date,
    project: str | None,
    focus_rows: list[tuple[date, int, float]],
    commit_timestamps: list[datetime],
    ai_session_timestamps: list[datetime],
    pressure_timestamps: list[datetime],
    target_tz: tzinfo | None = None,
) -> OperatorRhythm:
    """Pure-function rhythm composer.

    Input shapes (all pre-filtered to the window + project):

    - ``focus_rows``: list of (date, hour-of-day, active_minutes), as
      returned by ``activitywatch.circadian()`` (we only use active_min,
      not recovery; consumers wanting recovery should query directly).
      Hour values are taken at face value — they're already in the
      operator's wall-clock from ActivityWatch.
    - ``commit_timestamps``: tz-aware datetimes; bucketed in ``target_tz``.
    - ``ai_session_timestamps``: tz-aware datetimes of AI session
      ``start_ts`` (or canonical_session_date 00:00 fallback).
    - ``pressure_timestamps``: tz-aware datetimes of machine_episode
      ``start_ts``.
    - ``target_tz``: timezone to bucket commit/session/pressure
      timestamps into. None ⇒ system local time (whatever
      ``astimezone()`` resolves to). Tests should pass UTC for
      determinism.

    Partial-source detection: a source is "partial" if it produced zero
    entries for the window. (Genuine zero is indistinguishable from
    unloaded; callers can check the source readiness if they need to
    discriminate.)
    """
    buckets: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: {"focus_min": 0.0, "commit": 0, "ai": 0, "pressure": 0}
    )

    def _local(ts: datetime) -> datetime:
        if target_tz is not None:
            return ts.astimezone(target_tz) if ts.tzinfo else ts.replace(tzinfo=target_tz)
        return ts.astimezone() if ts.tzinfo else ts

    # NOTE: focus_rows is sourced from activitywatch.circadian() which is
    # AFK-derived (not-away minutes), not window-watcher derived. The
    # output field is named focus_min for backward-compat but the precise
    # semantic is "active minutes" (computer not idle). A separate bug
    # tracks the May 2026 window-watcher data gap.
    for d, hour, active_min in focus_rows:
        dow = d.weekday()
        buckets[(dow, int(hour))]["focus_min"] += float(active_min)

    for ts in commit_timestamps:
        local = _local(ts)
        buckets[(local.weekday(), local.hour)]["commit"] += 1

    for ts in ai_session_timestamps:
        local = _local(ts)
        buckets[(local.weekday(), local.hour)]["ai"] += 1

    for ts in pressure_timestamps:
        local = _local(ts)
        buckets[(local.weekday(), local.hour)]["pressure"] += 1

    items: list[HourBucket] = []
    for (dow, hour), agg in sorted(buckets.items()):
        if agg["focus_min"] == 0 and agg["commit"] == 0 and agg["ai"] == 0 and agg["pressure"] == 0:
            continue
        items.append(
            HourBucket(
                dow=dow,
                hour=hour,
                focus_min=round(agg["focus_min"], 1),
                commit_count=int(agg["commit"]),
                ai_session_count=int(agg["ai"]),
                pressure_episode_count=int(agg["pressure"]),
            )
        )

    partial: list[str] = []
    if not focus_rows:
        partial.append("activitywatch")
    if not commit_timestamps:
        partial.append("commit_fact")
    if not ai_session_timestamps:
        partial.append("ai_session")
    if not pressure_timestamps:
        partial.append("machine_episode")

    peak_focus = _peak_by(items, lambda b: b.focus_min)
    peak_commit = _peak_by(items, lambda b: float(b.commit_count))
    peak_combined = _peak_by(items, _combined_score)

    return OperatorRhythm(
        start=start,
        end=end,
        project=project,
        buckets=tuple(items),
        partial_sources=tuple(partial),
        peak_focus_hour=peak_focus,
        peak_commit_hour=peak_commit,
        peak_combined_hour=peak_combined,
    )


def _combined_score(bucket: HourBucket) -> float:
    """Normalized combined score for the 'best deep-work hour' summary.

    Weights chosen so that one commit ≈ 5 minutes of focus and one AI
    session ≈ 10 minutes of focus; pressure subtracts because high
    pressure correlates with the agent fighting the machine rather than
    shipping work. These are tuning constants, not load-bearing.
    """
    return (
        bucket.focus_min
        + 5.0 * bucket.commit_count
        + 10.0 * bucket.ai_session_count
        - 3.0 * bucket.pressure_episode_count
    )


def _peak_by(
    buckets: list[HourBucket],
    score: Callable[[HourBucket], float],
) -> tuple[int, int] | None:
    """Return (dow, hour) of the highest-scoring bucket, or None if empty."""
    if not buckets:
        return None
    best = max(buckets, key=score)
    if score(best) <= 0:
        return None
    return (best.dow, best.hour)


def render_rhythm_summary(rhythm: OperatorRhythm) -> str:
    """One-paragraph Markdown rendering of the peaks + caveat for partials."""
    lines = [
        f"Window {rhythm.start} → {rhythm.end}"
        + (f", project {rhythm.project}" if rhythm.project else ""),
    ]
    if rhythm.peak_combined_hour is not None:
        dow, hour = rhythm.peak_combined_hour
        lines.append(
            f"Peak deep-work hour: {WEEKDAY_NAMES[dow]} {hour:02d}:00 "
            f"(combined focus+commits+ai-sessions, pressure-adjusted)."
        )
    if rhythm.peak_focus_hour is not None:
        dow, hour = rhythm.peak_focus_hour
        lines.append(f"Peak ActivityWatch focus hour: {WEEKDAY_NAMES[dow]} {hour:02d}:00.")
    if rhythm.peak_commit_hour is not None:
        dow, hour = rhythm.peak_commit_hour
        lines.append(f"Peak commit hour: {WEEKDAY_NAMES[dow]} {hour:02d}:00.")
    if rhythm.partial_sources:
        lines.append(
            f"_Unobserved sources for this window:_ {', '.join(rhythm.partial_sources)}."
        )
    if not rhythm.buckets:
        lines.append("_No activity observed across any source in this window._")
    return " ".join(lines)


__all__ = [
    "HourBucket",
    "OperatorRhythm",
    "WEEKDAY_NAMES",
    "compute_operator_rhythm",
    "render_rhythm_summary",
]
