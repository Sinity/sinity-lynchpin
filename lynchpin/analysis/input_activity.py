"""Input-supported activity, separate from reported task dedication.

Input-bout elapsed time and bracketed gaps are conventions, not measurements
of attention. Raw foreground context is used without session or AFK enrichment.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Sequence

from lynchpin.core.primitives import Interval, merge_intervals
from lynchpin.core.serialization import jsonable
from lynchpin.sources.activitywatch_models import AWEvent
from lynchpin.sources.keylog import KeylogEvent, KeylogInputTrace


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps require an explicit timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ActivityPolicy:
    burst_gap_s: float = 2.0
    bout_gap_s: float = 30.0
    reading_gap_s: float = 300.0

    def __post_init__(self) -> None:
        values = (self.burst_gap_s, self.bout_gap_s, self.reading_gap_s)
        if not all(isfinite(v) and v > 0 for v in values) or not values[0] <= values[1] <= values[2]:
            raise ValueError("require finite positive burst <= bout <= reading gaps")


@dataclass(frozen=True)
class ContextSpan:
    id: str
    start: datetime
    end: datetime
    app: str
    title: str
    source_ref: str
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", aware(self.start))
        object.__setattr__(self, "end", aware(self.end))
        if self.end <= self.start:
            raise ValueError("context end must follow start")


_MODIFIERS = {
    "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL",
    "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA",
    "KEY_42", "KEY_54", "KEY_29", "KEY_97", "KEY_56", "KEY_100", "KEY_125", "KEY_126",
    "KEY_CAPSLOCK",
}
_NAVIGATION = {
    "KEY_LEFT", "KEY_RIGHT", "KEY_UP", "KEY_DOWN", "KEY_HOME", "KEY_END",
    "KEY_PAGEUP", "KEY_PAGEDOWN", "KEY_102", "KEY_103", "KEY_104", "KEY_105",
    "KEY_106", "KEY_107", "KEY_108", "KEY_109",
}
_EDITING = {"KEY_BACKSPACE", "KEY_DELETE", "KEY_111", "KEY_ENTER", "KEY_KPENTER", "KEY_TAB", "KEY_INSERT"}
_WHEELS = {"REL_WHEEL", "REL_HWHEEL", "REL_WHEEL_HI_RES", "REL_HWHEEL_HI_RES"}


def input_kind(event: KeylogEvent) -> str | None:
    if event.event == "pointer_rel":
        return "wheel" if event.code in _WHEELS and event.value != 0 else None
    if event.event in {"pointer_scroll", "pointer_wheel", "pointer_axis"}:
        return "wheel"
    if event.event == "pointer_button_press":
        return "button"
    if event.event != "press":
        return None
    key = event.keycode or ""
    if key in _MODIFIERS:
        return "modifier_key"
    if key in _NAVIGATION:
        return "navigation_key"
    if event.modifier_state_known and {"CTRL", "ALT", "SUPER"}.intersection(event.modifiers):
        return "shortcut_key"
    if key in _EDITING:
        return "editing_key"
    if event.changed is False:
        return "nontext_key"
    return "potential_text_key"


def event_ref(event: KeylogEvent) -> str:
    return f"{event.source_path}#L{event.source_line}" if event.source_path else f"{event.session}:{event.ts.isoformat()}"


def foreground_contexts(
    windows: Iterable[AWEvent], *, start: datetime, end: datetime, bucket: str | None = None,
) -> list[ContextSpan]:
    """Clip one foreground stream at the next record, retaining focus barriers.

    Multiple watcher/host streams require an explicit bucket choice. A zero-
    duration next record can end stale state; it must not first be discarded.
    """
    start, end = aware(start), aware(end)
    rows = [r for r in windows if r.start < end and r.end >= start and (bucket is None or r.bucket == bucket)]
    buckets = {r.bucket for r in rows}
    if len(buckets) > 1:
        raise ValueError("multiple foreground buckets; select one explicitly")
    rows.sort(key=lambda r: (r.start, r.end, str(r.data)))
    result = []
    for i, row in enumerate(rows):
        lo = max(start, aware(row.start))
        hi = min(end, aware(row.end), aware(rows[i + 1].start) if i + 1 < len(rows) else end)
        if hi <= lo:
            continue
        span = ContextSpan(
            f"window-{i}", lo, hi, str(row.data.get("app") or ""), str(row.data.get("title") or ""),
            f"activitywatch:{row.bucket}:{row.start.isoformat()}",
        )
        if (result and result[-1].end == lo
                and (result[-1].app, result[-1].title) == (span.app, span.title)
                and i > 0 and rows[i - 1].end > rows[i - 1].start):
            result[-1] = replace(result[-1], end=hi)
        else:
            result.append(span)
    return result


def load_foreground_contexts(
    *, start: datetime, end: datetime, db_path: Path | None = None, bucket: str | None = None,
) -> list[ContextSpan]:
    from lynchpin.sources.activitywatch_raw import window_events

    return foreground_contexts(
        window_events(start=start, end=end, db_path=db_path, ensure=False),
        start=start, end=end, bucket=bucket,
    )


def _seconds(intervals: Iterable[Interval]) -> float:
    # Normalize to UTC after the shared union helper for elapsed-time arithmetic.
    return sum((b.astimezone(UTC) - a.astimezone(UTC)).total_seconds() for a, b in merge_intervals(intervals))


def _intersection_seconds(left: Sequence[Interval], right: Sequence[Interval]) -> float:
    i = j = 0
    total = 0.0
    while i < len(left) and j < len(right):
        lo, hi = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if hi > lo:
            total += (hi.astimezone(UTC) - lo.astimezone(UTC)).total_seconds()
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return total


def reconstruct_activity(
    trace: KeylogInputTrace,
    contexts: Sequence[ContextSpan],
    *,
    policy: ActivityPolicy = ActivityPolicy(),
    reported_periods: Sequence[dict[str, Any]] = (),
    include_text: bool = False,
    anchors: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Account for every retained input once; attach evidence without inflating it.

    Bout spans end at the last input, never at an arbitrary post-input timeout.
    Longer gaps are separate candidates for reading/thinking/waiting, requiring
    two anchors in the same uninterrupted foreground span. Reported periods
    remain an independent evidence class, including during input silence.
    """
    start, end = aware(trace.start), aware(trace.end)
    if end <= start:
        raise ValueError("analysis end must follow start")
    ordered = sorted(contexts, key=lambda c: c.start)
    if len({c.id for c in ordered}) != len(ordered):
        raise ValueError("context IDs must be unique")
    if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
        raise ValueError("foreground contexts overlap")
    if any(c.start < start or c.end > end for c in ordered):
        raise ValueError("foreground contexts must be within analysis bounds")
    starts = [c.start for c in ordered]
    by_context: dict[str, list[KeylogEvent]] = {c.id: [] for c in ordered}
    counts: Counter[str] = Counter()
    unattributed = 0
    for ev in sorted(trace.events, key=lambda e: e.ts):
        ts = aware(ev.ts)
        if not start <= ts < end:
            continue
        kind = input_kind(ev)
        if kind is None:
            continue
        counts[kind] += 1
        index = bisect_right(starts, ts) - 1
        if index < 0 or ts >= ordered[index].end:
            unattributed += 1
        else:
            by_context[ordered[index].id].append(ev)

    bouts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    bout_intervals: list[Interval] = []
    for context in ordered:
        events = by_context[context.id]
        groups: list[list[KeylogEvent]] = []
        last_sessions: dict[str, str] = {}
        can_bridge: list[bool] = []
        for ev in events:
            family = "keyboard" if ev.event == "press" else "pointer"
            previous_session = last_sessions.get(family)
            restarted = bool(ev.session and previous_session and ev.session != previous_session)
            if ev.session:
                last_sessions[family] = ev.session
            gap = (ev.ts - groups[-1][-1].ts).total_seconds() if groups else 0
            if not groups or restarted or gap > policy.bout_gap_s:
                can_bridge.append(bool(groups) and not restarted)
                groups.append([ev])
            else:
                groups[-1].append(ev)

        local_bouts: list[Interval] = []
        local_gaps: list[Interval] = []
        burst_seconds = 0.0
        for index, group in enumerate(groups):
            lo, hi = aware(group[0].ts), aware(group[-1].ts)
            span_seconds = (hi - lo).total_seconds()
            within = sum(
                (b.ts - a.ts).total_seconds()
                for a, b in zip(group, group[1:])
                if (b.ts - a.ts).total_seconds() <= policy.burst_gap_s
            )
            burst_seconds += within
            local_bouts.append((lo, hi))
            bouts.append(dict(
                id=f"bout-{len(bouts)}", context_id=context.id, start=lo, end=hi,
                input_count=len(group), counts=dict(Counter(input_kind(ev) for ev in group)),
                span_seconds=span_seconds, within_burst_seconds=within,
                first_event_ref=event_ref(group[0]), last_event_ref=event_ref(group[-1]),
            ))
            if index and can_bridge[index]:
                previous = aware(groups[index - 1][-1].ts)
                gap = (lo - previous).total_seconds()
                if gap <= policy.reading_gap_s:
                    local_gaps.append((previous, lo))
                    candidates.append(dict(
                        context_id=context.id, start=previous, end=lo, seconds=gap,
                        interpretation="same_context_between_inputs",
                        possible_activities=["reading", "thinking", "waiting", "other"],
                        capture_continuity=trace.continuity,
                        left_event_ref=event_ref(groups[index - 1][-1]), right_event_ref=event_ref(group[0]),
                    ))
        bout_intervals.extend(local_bouts)
        duration = (context.end - context.start).total_seconds()
        bout_seconds, between_seconds = _seconds(local_bouts), _seconds(local_gaps)
        recent = {}
        for seconds in (60, 300):
            recent[str(seconds)] = _seconds((aware(ev.ts), min(context.end, aware(ev.ts) + timedelta(seconds=seconds))) for ev in events)
        summaries.append(dict(
            **asdict(context), context_seconds=duration, input_count=len(events),
            counts=dict(Counter(input_kind(ev) for ev in events)), bout_seconds=bout_seconds,
            within_burst_seconds=burst_seconds, between_input_seconds=between_seconds,
            unmeasured_context_seconds=max(0.0, duration - bout_seconds - between_seconds),
            input_recency_seconds=recent,
        ))

    reported: list[Interval] = []
    for period in reported_periods:
        a = aware(datetime.fromisoformat(period["start"].replace("Z", "+00:00")))
        b = aware(datetime.fromisoformat(period["end"].replace("Z", "+00:00")))
        if b <= a or not period.get("source_ref"):
            raise ValueError("reported periods require increasing bounds and a source_ref")
        lo, hi = max(start, a), min(end, b)
        if hi > lo:
            reported.append((lo, hi))
    reported_union = merge_intervals(reported)
    totals = {
        key: sum(row[key] for row in summaries)
        for key in ("context_seconds", "bout_seconds", "within_burst_seconds", "between_input_seconds", "unmeasured_context_seconds")
    }
    totals.update(input_count=sum(counts.values()), counts=dict(counts), unattributed_input_count=unattributed)
    totals["context_missing_seconds"] = (end - start).total_seconds() - totals["context_seconds"]
    totals["input_recency_seconds"] = {str(w): sum(row["input_recency_seconds"][str(w)] for row in summaries) for w in (60, 300)}
    result = dict(
        schema="lynchpin.input-activity.v1", start=start, end=end, policy=asdict(policy),
        totals=totals, contexts=summaries, input_bouts=bouts, gap_candidates=candidates,
        source=dict(files=trace.files, raw_event_counts=trace.event_counts, capture_continuity=trace.continuity),
        reported_periods=dict(
            evidence=list(reported_periods), union_seconds=_seconds(reported_union),
            bout_seconds_within=_intersection_seconds(merge_intervals(bout_intervals), reported_union),
            evidence_class="reported_task_dedication", added_to_input_totals=False,
        ),
        caveats=[
            "Input-bout time is elapsed time between nearby inputs, not physical key-down time or attention.",
            "Bracketed gaps are candidates for engagement, not proven reading or proven inactivity.",
            "No input after the last event is invented. Unmeasured time may include reading, thought or worry.",
            "Foreground titles identify contexts imperfectly, especially shared terminal titles.",
            "Recorder files do not establish continuous capture; keyboard release and autorepeat state may be absent.",
            "Low- and high-resolution wheel records may describe one gesture; counts are records, not gestures.",
            "Reported dedication is separate evidence; input silence does not invalidate it.",
        ],
    )
    if include_text or anchors:
        from .input_text import match_anchors, reconstruct_fragments

        fragments = reconstruct_fragments(trace.events, ordered)
        if include_text:
            result["text_fragments"] = fragments
        if anchors:
            result["anchor_matches"] = match_anchors(anchors, fragments)
    return jsonable(result)
