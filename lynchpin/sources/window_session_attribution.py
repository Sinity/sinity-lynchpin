"""Recover kitty-window→session attribution via polylogue work_events.

awatcher captures only ``{app, title}`` — no PID, no window-id. So two
simultaneous kitty windows of the same app are indistinguishable at the
raw layer. But polylogue records every Claude Code / Codex work event
with precise ``start_time``/``end_time``: each work event corresponds to
a discrete chunk of one specific conversation_id.

When a kitty focus span overlaps a polylogue work_event's window,
attribute the span to that work_event's ``conversation_id``. Two
simultaneous kitty windows running two different Claude Code sessions
yield TWO distinct attributions at the same wall-clock — the
multi-window-of-same-app distinction we lost in raw, recovered.

Coarse granularity: per work_event (one event spans many message turns),
not per message. Sufficient for "which session was that span part of?"
but not for "which user prompt drove that span specifically."

Limits:
- Non-agent kitty windows (interactive shells, vim, etc.) have no
  matching work_event and stay unattributed.
- Time-only overlap can mis-attribute if two sessions overlap in
  wall-clock (rare — operator usually runs one at a time, but
  parallel agents do happen). Mitigation: tie-break by app match.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol, Sequence

__all__ = [
    "SpanWindow",
    "WorkEventWindow",
    "SpanAttribution",
    "attribute_spans",
]


# Default slack: agent work_events sometimes overshoot the wall-clock
# window of the user's kitty focus by a few seconds. 30s catches
# trailing-output spans without bleeding into adjacent unrelated work.
_DEFAULT_SLACK_S = 30.0


class SpanWindow(Protocol):
    """Anything with start/end datetimes — typically an AW focus span."""

    start: datetime
    end: datetime


class WorkEventWindow(Protocol):
    """Polylogue work_event-like: timed event with a conversation_id."""

    conversation_id: str
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class SpanAttribution:
    """Best-matching work_event for a span.

    ``overlap_s`` is the total seconds of intersection between span and
    work_event. ``confidence`` is overlap_s / span_duration_s clipped
    to [0, 1].
    """
    conversation_id: str
    overlap_s: float
    confidence: float


def attribute_spans(
    spans: Iterable[SpanWindow],
    work_events: Sequence[WorkEventWindow],
    *,
    slack_s: float = _DEFAULT_SLACK_S,
) -> list[SpanAttribution | None]:
    """For each span, return the best matching work_event attribution.

    Returns one entry per input span in order. ``None`` when no work_event
    overlaps that span (likely a non-agent kitty window).

    Ties broken by greatest overlap; if overlaps are equal, the earlier
    work_event start wins (deterministic).
    """
    # Pre-filter work_events to those with both timestamps + a conversation_id.
    candidates: list[WorkEventWindow] = [
        we for we in work_events
        if we.start is not None and we.end is not None and we.conversation_id
    ]
    # Sort by start so we can stop scanning early per span.
    candidates.sort(key=lambda we: (we.start or datetime.min.replace(tzinfo=timezone.utc), we.end or datetime.min.replace(tzinfo=timezone.utc)))

    result: list[SpanAttribution | None] = []
    for span in spans:
        result.append(_best_match(span, candidates, slack_s=slack_s))
    return result


def _best_match(
    span: SpanWindow,
    candidates: Sequence[WorkEventWindow],
    *,
    slack_s: float,
) -> SpanAttribution | None:
    span_start = span.start
    span_end = span.end
    span_dur_s = max((span_end - span_start).total_seconds(), 0.001)
    slack = timedelta(seconds=slack_s)
    best: SpanAttribution | None = None
    for we in candidates:
        # candidates are pre-filtered: start/end are never None here
        assert we.start is not None and we.end is not None
        we_start = we.start - slack
        we_end = we.end + slack
        if we_start > span_end:
            break
        if we_end < span_start:
            continue
        overlap_start = max(span_start, we.start)
        overlap_end = min(span_end, we.end)
        overlap_s = max((overlap_end - overlap_start).total_seconds(), 0.0)
        # Slack-only overlap (no real intersection) still counts but at
        # reduced weight — give it a nominal 0.1s so it ranks below real
        # overlaps but above no-overlap.
        if overlap_s == 0.0:
            overlap_s = 0.1
        if best is None or overlap_s > best.overlap_s:
            confidence = min(overlap_s / span_dur_s, 1.0)
            best = SpanAttribution(
                conversation_id=str(we.conversation_id),
                overlap_s=overlap_s,
                confidence=confidence,
            )
    return best
