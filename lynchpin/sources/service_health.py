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
``active`` ``running`` configuration. Adjacent same-state rows merge.

Conservative semantics:
- If a unit has NO observations in a window, we emit a single
  ``ServiceDowntime`` of kind ``unobserved`` covering that window.
  Caller decides whether to treat that as downtime or as missing telemetry.
- A unit observed only once at ``active running`` doesn't prove uptime
  across the entire surrounding window; the next observation matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

    Algorithm: per unit, sweep observations in time order. Track the
    current state. Open an inactive interval when a non-active-running
    observation arrives; close when the next active+running observation
    arrives OR window_end is reached. If a unit has NO observations,
    emit a single ``unobserved`` interval covering [window_start, window_end].
    """
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
    open_start: datetime | None = None
    open_states: list[str] = []

    def _state_label(row: MachineServiceState) -> str:
        return f"{row.active_state or '?'}/{row.sub_state or '?'}"

    # If the first observation is after window_start AND it's inactive,
    # the unobserved prefix gets credited as unobserved (we don't know what
    # came before, but the unit transitioned somewhere in there).
    first = rows[0]
    if first.observed_at > window_start and not _is_running(first):
        yield ServiceDowntime(
            unit=unit, start=window_start, end=first.observed_at,
            kind="unobserved", observed_states=("unknown",),
        )

    for row in rows:
        if not _is_running(row):
            if open_start is None:
                open_start = max(row.observed_at, window_start)
            label = _state_label(row)
            if label not in open_states:
                open_states.append(label)
        else:
            if open_start is not None:
                yield ServiceDowntime(
                    unit=unit, start=open_start,
                    end=min(row.observed_at, window_end),
                    kind="inactive",
                    observed_states=tuple(open_states),
                )
                open_start = None
                open_states = []

    if open_start is not None:
        yield ServiceDowntime(
            unit=unit, start=open_start, end=window_end,
            kind="inactive", observed_states=tuple(open_states),
        )


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
