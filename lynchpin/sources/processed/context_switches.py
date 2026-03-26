"""Context switch metrics from AFK-trimmed app sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, Sequence

from .app_sessions import AppSession, iter_app_sessions
from .focus_loops import FocusLoop, iter_focus_loops


@dataclass(frozen=True)
class ContextSwitchMetrics:
    date: date
    total_switches: int
    project_switches: int
    mode_switches: int
    alternation_loop_count: int
    alternation_switches: int
    alternation_minutes: float
    alternation_share: float
    avg_focus_minutes: float
    longest_focus_minutes: float
    fragmentation_score: float


def iter_context_switch_metrics(
    *,
    start: date,
    end: date,
) -> Iterator[ContextSwitchMetrics]:
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    sessions = list(iter_app_sessions(start=start_dt, end=end_dt, min_duration_seconds=60))
    loops = list(iter_focus_loops(start=start_dt, end=end_dt))

    sessions_by_day: dict[date, list[AppSession]] = {}
    for session in sessions:
        sessions_by_day.setdefault(session.start.date(), []).append(session)
    loops_by_day: dict[date, list[FocusLoop]] = {}
    for loop in loops:
        loops_by_day.setdefault(loop.date, []).append(loop)

    current = start
    while current <= end:
        day_sessions = sorted(sessions_by_day.get(current, ()), key=lambda session: session.start)
        if len(day_sessions) < 2:
            current += timedelta(days=1)
            continue

        project_switches = 0
        mode_switches = 0
        total_switches = 0
        stretches = _focus_stretches(day_sessions)
        day_loops = loops_by_day.get(current, ())
        for left, right in zip(day_sessions, day_sessions[1:]):
            if _context_key(left) != _context_key(right):
                total_switches += 1
            if left.project and right.project and left.project != right.project:
                project_switches += 1
            if left.mode and right.mode and left.mode != right.mode:
                mode_switches += 1

        total_focus = sum(stretches)
        longest = max(stretches) if stretches else 0.0
        alternation_minutes = sum(loop.duration_minutes for loop in day_loops)
        yield ContextSwitchMetrics(
            date=current,
            total_switches=total_switches,
            project_switches=project_switches,
            mode_switches=mode_switches,
            alternation_loop_count=len(day_loops),
            alternation_switches=sum(loop.switch_count for loop in day_loops),
            alternation_minutes=alternation_minutes,
            alternation_share=(alternation_minutes / total_focus) if total_focus > 0 else 0.0,
            avg_focus_minutes=(sum(stretches) / len(stretches)) if stretches else 0.0,
            longest_focus_minutes=longest,
            fragmentation_score=(
                max(0.0, min(1.0, 1.0 - (longest / total_focus)))
                if total_focus > 0
                else 0.0
            ),
        )
        current += timedelta(days=1)


def _focus_stretches(sessions: Sequence[AppSession]) -> list[float]:
    if not sessions:
        return []
    stretches: list[float] = []
    current_key = _context_key(sessions[0])
    current_minutes = sessions[0].duration_seconds / 60.0
    current_end = sessions[0].end
    for session in sessions[1:]:
        gap_minutes = max((session.start - current_end).total_seconds(), 0.0) / 60.0
        if _context_key(session) == current_key and gap_minutes <= 5.0:
            current_minutes += gap_minutes + (session.duration_seconds / 60.0)
            current_end = session.end
            continue
        stretches.append(current_minutes)
        current_key = _context_key(session)
        current_minutes = session.duration_seconds / 60.0
        current_end = session.end
    stretches.append(current_minutes)
    return stretches


def _context_key(session: AppSession) -> str:
    if session.project:
        return f"project:{session.project}"
    if session.mode:
        return f"mode:{session.mode}"
    return f"app:{session.app}"
