"""Detect downtime intervals for capture services via machine telemetry.

Lynchpin treats absence of data as "operator was inactive" by default. That's
wrong when the absence is caused by a stopped/failed service. Knowing
``activitywatch.service``, ``activitywatch-watcher-awatcher.service``, and
``polylogued.service`` were genuinely down in a window lets downstream
analytics distinguish:

    "no AW window events because operator AFK"
    "no AW window events because watcher unit was inactive"
    "no AI session because polylogued was down at that moment"

Inputs: ``MachineServiceState`` rows from
``lynchpin.sources.machine.service_states``.

Output: ``ServiceDowntime`` intervals where a unit was not in the
``active`` ``running`` configuration. Adjacent intervals of the same kind merge.

Conservative semantics:
    - State is carried for at most ``MAX_OBSERVATION_GAP`` after each sample;
      longer gaps and unbounded window edges are ``unobserved``.
    - Only a predecessor can establish the state at ``window_start``. Rows at
      or after ``window_end`` do not contribute intervals.
- A unit observed only once at ``active running`` doesn't prove uptime
  across the entire surrounding window; the next observation matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Iterator, Sequence

from .machine_models import MachineServiceState

__all__ = [
    "CAPTURE_SERVICE_UNITS",
    "ServiceDowntime",
    "downtime_intervals",
    "service_uptime_summary",
]


# Units that produce lynchpin's capture data. When down, absence of
# data is service downtime, not operator AFK.
CAPTURE_SERVICE_UNITS: tuple[str, ...] = (
    "activitywatch.service",
    "activitywatch-watcher-awatcher.service",
    "polylogued.service",
)

# Sinnix's service-state collector normally samples every 10 seconds. Allow
# three missed samples before treating the remaining time as unobserved.
MAX_OBSERVATION_GAP = timedelta(seconds=30)


@dataclass(frozen=True)
class ServiceDowntime:
    """A contiguous interval during which a unit was not active+running.

    ``kind``:
      - ``inactive`` — observed in non-active state (failed/inactive/activating)
      - ``unobserved`` — no telemetry rows in the window; downstream
        analysis decides whether to treat as gap

    ``observed_states`` lists distinct (active_state, sub_state) pairs seen
    during the interval; ``"unknown"`` for the unobserved case.
    """
    unit: str
    start: datetime
    end: datetime
    kind: str
    observed_states: tuple[str, ...] = ()


def downtime_intervals(
    states: Iterable[MachineServiceState],
    *,
    window_start: datetime,
    window_end: datetime,
    units: Sequence[str] = CAPTURE_SERVICE_UNITS,
) -> Iterator[ServiceDowntime]:
    """Yield non-active-running intervals per unit within [window_start, window_end].

    ``states`` should be a stream of observations across one or more units;
    they are filtered to ``units`` here. Order need not be sorted on input.
    Each unit's observations are sorted by ``observed_at`` before scanning.

    The machine collector normally samples every 10 seconds, so three missed
    samples is the maximum state hold. This bounds both uptime claims and
    downtime claims instead of extrapolating indefinitely from a stale row.
    """
    if window_end <= window_start:
        return
    unit_set = set(units)
    by_unit: dict[str, list[MachineServiceState]] = {}
    for state in states:
        if state.unit in unit_set:
            by_unit.setdefault(state.unit, []).append(state)

    for unit in units:
        rows = sorted(by_unit.get(unit, []), key=lambda s: s.observed_at)
        if not rows:
            yield ServiceDowntime(
                unit=unit, start=window_start, end=window_end,
                kind="unobserved", observed_states=("unknown",),
            )
            continue
        yield from _sweep_unit(unit, rows, window_start, window_end)


def _is_running(state: MachineServiceState) -> bool:
    return state.active_state == "active" and state.sub_state == "running"


def _sweep_unit(
    unit: str,
    rows: Sequence[MachineServiceState],
    window_start: datetime,
    window_end: datetime,
) -> Iterator[ServiceDowntime]:
    def _state_label(row: MachineServiceState) -> str:
        return f"{row.active_state or '?'}/{row.sub_state or '?'}"
    prior = [row for row in rows if row.observed_at < window_start]
    in_window = [
        row for row in rows
        if window_start <= row.observed_at < window_end
    ]
    current = prior[-1] if prior else None
    cursor = window_start
    intervals: list[ServiceDowntime] = []

    def append(
        start: datetime,
        end: datetime,
        kind: str,
        states: tuple[str, ...],
    ) -> None:
        if end <= start or kind == "active":
            return
        if intervals and intervals[-1].end == start and intervals[-1].kind == kind:
            previous = intervals[-1]
            combined = tuple(dict.fromkeys(previous.observed_states + states))
            intervals[-1] = ServiceDowntime(unit, previous.start, end, kind, combined)
        else:
            intervals.append(ServiceDowntime(unit, start, end, kind, states))

    # A predecessor may establish the state at the left edge only while it is
    # within the sampling hold. Otherwise the prefix is explicitly unknown.
    if current is None or window_start - current.observed_at > MAX_OBSERVATION_GAP:
        current = None

    for row in in_window:
        if row.observed_at > cursor:
            if current is None:
                append(cursor, row.observed_at, "unobserved", ("unknown",))
            else:
                held_until = min(row.observed_at, current.observed_at + MAX_OBSERVATION_GAP)
                kind = "active" if _is_running(current) else "inactive"
                append(cursor, held_until, kind, (_state_label(current),))
                append(held_until, row.observed_at, "unobserved", ("unknown",))
        current = row
        cursor = row.observed_at

    if cursor < window_end:
        if current is None:
            append(cursor, window_end, "unobserved", ("unknown",))
        else:
            held_until = min(window_end, current.observed_at + MAX_OBSERVATION_GAP)
            kind = "active" if _is_running(current) else "inactive"
            append(cursor, held_until, kind, (_state_label(current),))
            append(held_until, window_end, "unobserved", ("unknown",))

    yield from intervals


def service_uptime_summary(
    states: Iterable[MachineServiceState],
    *,
    window_start: datetime,
    window_end: datetime,
    units: Sequence[str] = CAPTURE_SERVICE_UNITS,
) -> dict[str, dict[str, float]]:
    """Per-unit uptime fraction over [window_start, window_end].

    Returns ``{unit: {"downtime_s": float, "uptime_fraction": float}}``.
    ``uptime_fraction = 1 - downtime_s/window_s``. Unobserved intervals
    count toward downtime here — they are not provably up.
    """
    window_s = (window_end - window_start).total_seconds()
    result: dict[str, dict[str, float]] = {
        unit: {"downtime_s": 0.0, "uptime_fraction": 1.0}
        for unit in units
    }
    for interval in downtime_intervals(
        states, window_start=window_start, window_end=window_end, units=units,
    ):
        dt = (interval.end - interval.start).total_seconds()
        result[interval.unit]["downtime_s"] += max(dt, 0.0)
    if window_s > 0:
        for stats in result.values():
            stats["uptime_fraction"] = max(
                0.0, 1.0 - stats["downtime_s"] / window_s
            )
    return result
