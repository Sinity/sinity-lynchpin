"""Processed app sessions: coalesced ActivityWatch window signals into contiguous app usage spans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

from ...trajectory.signal import TrajectorySignal, load_signals


@dataclass(frozen=True)
class AppSession:
    app: str
    start: datetime
    end: datetime
    duration_seconds: float
    title_dominant: str
    title_count: int
    titles: tuple[str, ...]
    mode: Optional[str]
    project: Optional[str]
    interruptions: int


def iter_app_sessions(
    *,
    start: datetime,
    end: datetime,
    min_duration_seconds: float = 60,
    merge_gap_seconds: float = 120,
) -> Iterator[AppSession]:
    """Yield contiguous app usage sessions from ActivityWatch window signals.

    Consecutive signals for the same app within *merge_gap_seconds* are merged
    into a single session.  Brief interruptions (<30s to another app then back)
    increment the interruptions counter without breaking the session.
    """
    signals = load_signals(start=start, end=end)
    window_signals = [s for s in signals if s.source == "activitywatch.window" and s.app]

    if not window_signals:
        return

    # ---- merge consecutive same-app signals with interruption handling ----
    groups: list[_SessionAccumulator] = []
    acc: Optional[_SessionAccumulator] = None

    for sig in window_signals:
        assert sig.app is not None
        if acc is not None and _should_merge(acc, sig, merge_gap_seconds):
            acc.extend(sig)
            continue
        # Check if this is a brief interruption: different app, short, and next
        # signal returns to the accumulated app.  We peek ahead lazily by
        # deferring the decision: record the interruption candidate and only
        # break the session if the *next* signal doesn't return to the same app.
        if acc is not None and acc.pending_interruption is not None:
            # Previous signal was a short interruption candidate.  This signal
            # determines whether it actually was an interruption (same app as
            # acc) or a real context switch.
            pi = acc.pending_interruption
            if sig.app == acc.app and (sig.start - pi.end).total_seconds() < merge_gap_seconds:
                # The interruption resolved back to the same app.
                acc.interruptions += 1
                acc.extend(sig)
                acc.pending_interruption = None
                continue
            else:
                # Real context switch — flush the accumulated session without
                # the pending interruption (it belongs to a new session).
                acc.pending_interruption = None
                groups.append(acc)
                acc = None
                # Fall through to start a new accumulator for `pi` then `sig`.
                # We skip creating a session for `pi` itself (it was <30s) and
                # just start fresh from `sig`.

        if acc is not None:
            gap = (sig.start - acc.end).total_seconds()
            if (
                sig.app != acc.app
                and sig.duration_seconds < 30
                and gap < merge_gap_seconds
            ):
                # Candidate brief interruption — defer decision.
                acc.pending_interruption = sig
                continue
            # Genuine context switch or gap too large.
            groups.append(acc)
            acc = None

        acc = _SessionAccumulator(sig)

    if acc is not None:
        groups.append(acc)

    # ---- emit AppSession objects ----
    for group in groups:
        duration = (group.end - group.start).total_seconds()
        if duration < min_duration_seconds:
            continue
        dominant_title, unique_titles = _compute_title_stats(group.title_durations)
        yield AppSession(
            app=group.app,
            start=group.start,
            end=group.end,
            duration_seconds=round(duration, 3),
            title_dominant=dominant_title,
            title_count=len(unique_titles),
            titles=unique_titles,
            mode=group.mode_hint,
            project=group.project_hint,
            interruptions=group.interruptions,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SessionAccumulator:
    __slots__ = (
        "app",
        "start",
        "end",
        "mode_hint",
        "project_hint",
        "title_durations",
        "interruptions",
        "pending_interruption",
    )

    def __init__(self, sig: TrajectorySignal) -> None:
        assert sig.app is not None
        self.app: str = sig.app
        self.start: datetime = sig.start
        self.end: datetime = sig.end
        self.mode_hint: Optional[str] = sig.mode_hint
        self.project_hint: Optional[str] = sig.project_hint
        self.title_durations: dict[str, float] = defaultdict(float)
        self.interruptions: int = 0
        self.pending_interruption: Optional[TrajectorySignal] = None
        self._record_title(sig)

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def extend(self, sig: TrajectorySignal) -> None:
        if sig.end > self.end:
            self.end = sig.end
        self._record_title(sig)

    def _record_title(self, sig: TrajectorySignal) -> None:
        title = sig.title or "(untitled)"
        self.title_durations[title] += sig.duration_seconds


def _should_merge(acc: _SessionAccumulator, sig: TrajectorySignal, merge_gap: float) -> bool:
    if sig.app != acc.app:
        return False
    gap = (sig.start - acc.end).total_seconds()
    return gap < merge_gap


def _compute_title_stats(
    title_durations: dict[str, float],
) -> tuple[str, tuple[str, ...]]:
    if not title_durations:
        return "(untitled)", ()
    sorted_titles = sorted(title_durations.items(), key=lambda t: t[1], reverse=True)
    dominant = sorted_titles[0][0]
    unique = tuple(t[0] for t in sorted_titles)
    return dominant, unique
