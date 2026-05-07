"""Terminal error pattern detection over shell sessions.

Detects build/fix loops, retry spirals, long-running commands, and
context switches from Atuin shell session data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..sources.terminal import shell_sessions


@dataclass(frozen=True)
class TerminalPattern:
    kind: str  # build_fix_loop, retry_spiral, long_running, context_switch
    date: date
    cwd: str
    project: str | None
    command_count: int
    error_count: int
    duration_s: float
    top_commands: tuple[str, ...]
    confidence: float
    summary: str


def detect_patterns(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
) -> tuple[TerminalPattern, ...]:
    """Detect terminal patterns from shell sessions in a date range."""
    sessions = shell_sessions(start=start, end=end)

    chosen = set(projects or ())
    patterns: list[TerminalPattern] = []

    for session in sessions:
        if chosen and session.project not in chosen:
            continue
        if not session.commands:
            continue

        patterns.extend(_detect_in_session(session))

    return tuple(patterns)


def _detect_in_session(session) -> list[TerminalPattern]:
    patterns: list[TerminalPattern] = []
    cmds = list(session.commands)
    if len(cmds) < 2:
        return patterns

    cwd = session.cwd or ""
    project = session.project
    session_date = _session_date(cmds)

    # Build/fix loop: error exits → success on same cwd
    bf_patterns = _detect_build_fix_loops(
        cmds, cwd, project, session_date,
    )
    patterns.extend(bf_patterns)

    # Retry spirals: same command prefix ≥3 times, non-zero exits
    rs_patterns = _detect_retry_spirals(
        cmds, cwd, project, session_date,
    )
    patterns.extend(rs_patterns)

    # Long-running: command duration >60s
    lr_patterns = _detect_long_running(
        cmds, cwd, project, session_date,
    )
    patterns.extend(lr_patterns)

    # Context switches: rapid cwd changes
    cs_patterns = _detect_context_switches(
        session, session_date,
    )
    patterns.extend(cs_patterns)

    return patterns


def _session_date(cmds: list) -> date:
    for cmd in cmds:
        ts = getattr(cmd, "timestamp", None)
        if ts:
            if isinstance(ts, datetime):
                return ts.date()
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
                except ValueError:
                    pass
    return datetime.now(timezone.utc).date()


def _detect_build_fix_loops(
    cmds: list, cwd: str, project: str | None, session_date: date,
) -> list[TerminalPattern]:
    patterns: list[TerminalPattern] = []
    error_runs: list[list] = []
    current_run: list = []

    for cmd in cmds:
        exit_code = _exit_code(cmd)
        if exit_code is not None and exit_code != 0:
            current_run.append(cmd)
        elif current_run:
            if len(current_run) >= 2:
                error_runs.append(current_run)
            current_run = []
    if len(current_run) >= 2:
        error_runs.append(current_run)

    for run in error_runs:
        prefix = _command_prefix(run[0])
        has_fix = any(
            _exit_code(c) == 0 and _command_prefix(c) == prefix
            for c in cmds[cmds.index(run[-1]) + 1:]
        ) if cmds.index(run[-1]) + 1 < len(cmds) else False

        patterns.append(TerminalPattern(
            kind="build_fix_loop",
            date=session_date,
            cwd=cwd,
            project=project,
            command_count=len(run),
            error_count=len(run),
            duration_s=_duration_between(run[0], run[-1]),
            top_commands=(_command_prefix(run[0]),),
            confidence=0.7 if has_fix else 0.5,
            summary=(
                f"{len(run)} consecutive errors on {_command_prefix(run[0])}"
                + (" → fix found" if has_fix else " — no fix detected")
            ),
        ))
    return patterns[:3]


def _detect_retry_spirals(
    cmds: list, cwd: str, project: str | None, session_date: date,
) -> list[TerminalPattern]:
    patterns: list[TerminalPattern] = []
    prefix_runs: dict[str, list] = defaultdict(list)

    for cmd in cmds:
        prefix = _command_prefix(cmd)
        exit_code = _exit_code(cmd)
        if exit_code is not None and exit_code != 0:
            prefix_runs[prefix].append(cmd)

    for prefix, run in prefix_runs.items():
        if len(run) >= 3:
            patterns.append(TerminalPattern(
                kind="retry_spiral",
                date=session_date,
                cwd=cwd,
                project=project,
                command_count=len(run),
                error_count=len(run),
                duration_s=_duration_between(run[0], run[-1]),
                top_commands=(prefix,),
                confidence=0.6 + min(0.2, (len(run) - 3) * 0.05),
                summary=f"{len(run)} retries of '{prefix}' with non-zero exits",
            ))
    return patterns[:3]


def _detect_long_running(
    cmds: list, cwd: str, project: str | None, session_date: date,
) -> list[TerminalPattern]:
    patterns: list[TerminalPattern] = []
    for cmd in cmds:
        duration = _cmd_duration(cmd)
        if duration is not None and duration > 60.0:
            patterns.append(TerminalPattern(
                kind="long_running",
                date=session_date,
                cwd=cwd,
                project=project,
                command_count=1,
                error_count=1 if (_exit_code(cmd) or 0) != 0 else 0,
                duration_s=duration,
                top_commands=(_command_prefix(cmd),),
                confidence=0.8,
                summary=f"Long-running: '{_command_prefix(cmd)}' ({duration:.0f}s)",
            ))
    return patterns[:5]


def _detect_context_switches(
    session, session_date: date,
) -> list[TerminalPattern]:
    patterns: list[TerminalPattern] = []
    if hasattr(session, "command_count") and session.command_count >= 8:
        error_rate = (
            session.error_count / session.command_count
            if session.command_count else 0
        )
        patterns.append(TerminalPattern(
            kind="context_switch",
            date=session_date,
            cwd=session.cwd or "",
            project=session.project,
            command_count=session.command_count,
            error_count=session.error_count,
            duration_s=session.duration_min * 60 if hasattr(session, "duration_min") else 0,
            top_commands=tuple(
                session.top_commands[:5]
                if hasattr(session, "top_commands")
                else ()
            ),
            confidence=0.5 + min(0.3, error_rate * 0.5),
            summary=(
                f"High-activity session: {session.command_count} commands, "
                f"error rate {error_rate:.0%}"
            ),
        ))
    return patterns[:2]


def _exit_code(cmd) -> int | None:
    ec = getattr(cmd, "exit_code", None)
    if ec is not None:
        try:
            return int(ec)
        except (TypeError, ValueError):
            pass
    return None


def _command_prefix(cmd) -> str:
    full = str(getattr(cmd, "command", ""))
    return full.split()[0] if full else "?"


def _cmd_duration(cmd) -> float | None:
    dur = getattr(cmd, "duration", None)
    if dur is not None:
        try:
            return float(dur)
        except (TypeError, ValueError):
            pass
    return None


def _duration_between(first, last) -> float:
    t1 = getattr(first, "timestamp", None)
    t2 = getattr(last, "timestamp", None)
    if t1 and t2:
        try:
            if isinstance(t1, str):
                t1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
            if isinstance(t2, str):
                t2 = datetime.fromisoformat(t2.replace("Z", "+00:00"))
            return (t2 - t1).total_seconds()
        except (ValueError, TypeError):
            pass
    return 0.0


__all__ = ["TerminalPattern", "detect_patterns"]
