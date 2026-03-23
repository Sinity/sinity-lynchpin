"""Context switch metrics: measure focus fragmentation from trajectory signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator


@dataclass(frozen=True)
class ContextSwitchMetrics:
    date: date
    total_switches: int
    project_switches: int
    mode_switches: int
    avg_focus_minutes: float
    longest_focus_minutes: float
    fragmentation_score: float  # 0=focused, 1=fragmented


def iter_context_switch_metrics(
    *, start: date, end: date
) -> Iterator[ContextSwitchMetrics]:
    from ...trajectory.signal import load_signals

    d = start
    while d <= end:
        dt_start = datetime(d.year, d.month, d.day)
        dt_end = dt_start + timedelta(days=1)
        try:
            signals = load_signals(start=dt_start, end=dt_end)
        except Exception:
            d += timedelta(days=1)
            continue

        non_afk = [
            s
            for s in signals
            if s.source != "activitywatch.afk" and s.project_hint
        ]
        if len(non_afk) < 2:
            d += timedelta(days=1)
            continue

        project_switches = sum(
            1
            for i in range(1, len(non_afk))
            if non_afk[i].project_hint != non_afk[i - 1].project_hint
        )
        mode_switches = sum(
            1
            for i in range(1, len(non_afk))
            if non_afk[i].mode_hint != non_afk[i - 1].mode_hint
        )

        # Focus stretches between project switches
        stretches = []
        stretch_start = non_afk[0].start
        for i in range(1, len(non_afk)):
            if non_afk[i].project_hint != non_afk[i - 1].project_hint:
                stretch_dur = (
                    non_afk[i - 1].end - stretch_start
                ).total_seconds() / 60
                if stretch_dur > 0:
                    stretches.append(stretch_dur)
                stretch_start = non_afk[i].start
        final = (non_afk[-1].end - stretch_start).total_seconds() / 60
        if final > 0:
            stretches.append(final)

        total_active = sum(stretches) if stretches else 1.0
        longest = max(stretches) if stretches else 0.0

        yield ContextSwitchMetrics(
            date=d,
            total_switches=project_switches + mode_switches,
            project_switches=project_switches,
            mode_switches=mode_switches,
            avg_focus_minutes=(
                sum(stretches) / len(stretches) if stretches else 0.0
            ),
            longest_focus_minutes=longest,
            fragmentation_score=(
                max(0.0, min(1.0, 1.0 - longest / total_active))
                if total_active > 0
                else 0.0
            ),
        )
        d += timedelta(days=1)
