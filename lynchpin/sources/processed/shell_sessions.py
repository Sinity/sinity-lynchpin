"""Processed shell sessions: coalesced Atuin commands grouped by cwd into contiguous work spans."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Optional

from ...metrics.productivity import categorise_command
from ...sources.captures.atuin import iter_commands

_LAST_CMD_FALLBACK = timedelta(seconds=5)
_PROJECT_RE = re.compile(r"/realm/project/([^/]+)")


@dataclass(frozen=True)
class ShellSession:
    cwd: str
    project: Optional[str]
    start: datetime
    end: datetime
    duration_seconds: float
    command_count: int
    error_count: int
    commands_summary: tuple[str, ...]
    category: str


def iter_shell_sessions(
    *,
    start: datetime,
    end: datetime,
    gap_seconds: float = 300,
) -> Iterator[ShellSession]:
    """Yield shell sessions grouped by cwd from Atuin history.

    Consecutive commands in the same working directory within *gap_seconds*
    form one session.  A cwd change or a gap larger than *gap_seconds* starts
    a new session.
    """
    acc: Optional[_ShellAccumulator] = None

    for cmd in iter_commands(start=start, end=end):
        cwd = cmd.cwd or "(unknown)"
        if acc is not None:
            gap = (cmd.timestamp - acc.last_ts).total_seconds()
            if cwd == acc.cwd and gap <= gap_seconds:
                acc.add(cmd)
                continue
            # Different cwd or gap too large — flush.
            yield acc.to_session()
            acc = None

        acc = _ShellAccumulator(cwd, cmd)

    if acc is not None:
        yield acc.to_session()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _ShellAccumulator:
    __slots__ = ("cwd", "first_ts", "last_ts", "commands", "exit_codes", "cmd_prefixes")

    def __init__(self, cwd: str, cmd: object) -> None:
        self.cwd = cwd
        self.first_ts: datetime = cmd.timestamp  # type: ignore[union-attr]
        self.last_ts: datetime = cmd.timestamp  # type: ignore[union-attr]
        self.commands: list[object] = [cmd]
        self.exit_codes: list[Optional[int]] = [cmd.exit_code]  # type: ignore[union-attr]
        self.cmd_prefixes: Counter[str] = Counter()
        self._record_prefix(cmd)

    def add(self, cmd: object) -> None:
        self.commands.append(cmd)
        ts = cmd.timestamp  # type: ignore[union-attr]
        if ts > self.last_ts:
            self.last_ts = ts
        self.exit_codes.append(cmd.exit_code)  # type: ignore[union-attr]
        self._record_prefix(cmd)

    def _record_prefix(self, cmd: object) -> None:
        text = getattr(cmd, "command", "") or ""
        prefix = text.strip().split()[0] if text.strip() else "(empty)"
        self.cmd_prefixes[prefix] += 1

    def to_session(self) -> ShellSession:
        duration = (self.last_ts - self.first_ts).total_seconds() + _LAST_CMD_FALLBACK.total_seconds()
        project = _extract_project(self.cwd)
        category = categorise_command(self.cwd, "")
        error_count = sum(1 for ec in self.exit_codes if ec is not None and ec != 0)
        top_prefixes = tuple(
            prefix for prefix, _ in self.cmd_prefixes.most_common(5)
        )
        return ShellSession(
            cwd=self.cwd,
            project=project,
            start=self.first_ts,
            end=self.last_ts + _LAST_CMD_FALLBACK,
            duration_seconds=round(duration, 3),
            command_count=len(self.commands),
            error_count=error_count,
            commands_summary=top_prefixes,
            category=category,
        )


def _extract_project(cwd: str) -> Optional[str]:
    m = _PROJECT_RE.search(cwd)
    return m.group(1) if m else None
