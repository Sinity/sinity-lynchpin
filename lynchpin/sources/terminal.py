"""Terminal source: Atuin shell commands + sessions, asciinema/kitty terminal recordings.

Absorbs: captures/atuin, captures/terminal_capture*, processed/shell_sessions, metrics/productivity.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

from ..core.config import get_config
from ..core.parse import as_local
from ..core.projects import canonical_project_name
from ..core.primitives import TopN, group_by_gap, date_to_dt_range

__all__ = [
    "AtuinCommand",
    "ShellSession",
    "TerminalRecording",
    "DailyTerminalActivity",
    "commands",
    "shell_sessions",
    "recordings",
    "daily_terminal_activity",
    "daily_activity",
]

# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AtuinCommand:
    timestamp: datetime
    duration_ns: Optional[int]
    exit_code: Optional[int]
    cwd: Optional[str]
    command: str


@dataclass(frozen=True)
class ShellSession:
    cwd: str
    project: Optional[str]
    start: datetime
    end: datetime
    duration_s: float
    command_count: int
    error_count: int
    commands_summary: tuple[str, ...]
    category: str


@dataclass(frozen=True)
class TerminalRecording:
    session_id: str
    path: str
    created_at: Optional[datetime]
    duration_s: Optional[float]
    title: Optional[str]
    shell: Optional[str]


# ══════════════════════════════════════════════════════════════════════════════
# Atuin: raw shell commands
# ══════════════════════════════════════════════════════════════════════════════


def commands(*, start: Optional[datetime] = None, end: Optional[datetime] = None) -> Iterator[AtuinCommand]:
    """Yield shell commands from the canonical Atuin materialization."""
    path = canonical_atuin_history_path()
    if not path.exists():
        raise FileNotFoundError(
            f"canonical Atuin materialization is missing: {path}. "
            "Run python -m lynchpin.ingest.terminal_materialize."
        )
    start_cmp = as_local(start) if start else None
    end_cmp = as_local(end) if end else None
    for command in _commands_from_ndjson(path):
        timestamp = as_local(command.timestamp)
        if start_cmp and timestamp < start_cmp:
            continue
        if end_cmp and timestamp >= end_cmp:
            continue
        yield command


def canonical_atuin_history_path() -> Path:
    cfg = get_config()
    return cfg.captures_root / "shell/atuin/history.ndjson"


def commands_from_atuin_db(db: Path) -> Iterator[AtuinCommand]:
    """Yield shell commands directly from an Atuin SQLite DB for materializers."""
    with sqlite3.connect(str(db)) as conn:
        unit = _detect_unit(conn)
        query = "SELECT timestamp, duration, exit, cwd, command FROM history"
        query += " ORDER BY timestamp"
        for row in conn.execute(query):
            yield AtuinCommand(
                timestamp=_from_unit(row[0], unit),
                duration_ns=row[1], exit_code=row[2], cwd=row[3], command=row[4],
            )


def _commands_from_ndjson(path: Path) -> Iterator[AtuinCommand]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            yield AtuinCommand(
                timestamp=datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00")),
                duration_ns=payload.get("duration_ns"),
                exit_code=payload.get("exit_code"),
                cwd=payload.get("cwd"),
                command=str(payload.get("command") or ""),
            )


# ══════════════════════════════════════════════════════════════════════════════
# Shell sessions: gap-based grouping of commands by cwd
# ══════════════════════════════════════════════════════════════════════════════

_LAST_CMD_FALLBACK = timedelta(seconds=5)
_PROJECT_RE = re.compile(r"/realm/project/([^/]+)")


def shell_sessions(*, start: datetime, end: datetime, gap_seconds: float = 300) -> list[ShellSession]:
    result: list[ShellSession] = []
    for g in group_by_gap(
        commands(start=start, end=end),
        start_of=lambda c: c.timestamp,
        end_of=lambda c: c.timestamp + _LAST_CMD_FALLBACK,
        max_gap=gap_seconds,
        compatible=lambda a, b: (a.cwd or "") == (b.cwd or ""),
    ):
        cwd = g.items[0].cwd or "(unknown)"
        project = _extract_project(cwd)
        error_count = sum(1 for c in g.items if c.exit_code is not None and c.exit_code != 0)
        prefixes: Counter[str] = Counter()
        for c in g.items:
            prefix = (c.command or "").strip().split()[0] if (c.command or "").strip() else "(empty)"
            prefixes[prefix] += 1
        duration = (g.end - g.start).total_seconds()
        result.append(ShellSession(
            cwd=cwd, project=project, start=g.start, end=g.end,
            duration_s=round(duration, 3), command_count=len(g.items),
            error_count=error_count,
            commands_summary=tuple(p for p, _ in prefixes.most_common(5)),
            category=_categorise_command(cwd),
        ))
    return result


def _extract_project(cwd: str) -> Optional[str]:
    m = _PROJECT_RE.search(cwd)
    return canonical_project_name(m.group(1)) if m else None


def _categorise_command(cwd: str) -> str:
    lowered = cwd.strip().lower()
    if "project/sinex" in lowered or lowered.rstrip("/").endswith("sinex"):
        return "development:sinex"
    if "sinnix" in lowered:
        return "infrastructure:sinnix"
    if "/realm/project/" in lowered:
        return "development:other"
    if lowered.startswith(("/realm/home", "/home")):
        return "home"
    return "misc"


# ══════════════════════════════════════════════════════════════════════════════
# Terminal recordings: asciinema .cast files
# ══════════════════════════════════════════════════════════════════════════════


def recordings(
    *,
    start: Optional[Union[date, datetime]] = None,
    end: Optional[Union[date, datetime]] = None,
) -> Iterator[TerminalRecording]:
    """Parse asciinema .cast files from the asciinema captures directory.

    ``start`` and ``end`` accept either ``date`` or ``datetime``. Dates
    are normalized to local-midnight datetimes so the comparison against
    each recording's ``created_at`` (a datetime) works.
    """
    from ..core.parse import local_tz

    cfg = get_config()
    root = cfg.asciinema_root
    if not root.exists():
        return

    tz = local_tz()
    if isinstance(start, date) and not isinstance(start, datetime):
        start = datetime.combine(start, time.min, tzinfo=tz)
    if isinstance(end, date) and not isinstance(end, datetime):
        end = datetime.combine(end, time.min, tzinfo=tz)

    cast_files = sorted(root.rglob("*.cast"))
    for cast_path in cast_files:
        rec = _parse_cast_file(cast_path)
        if rec is None:
            continue
        if start and rec.created_at and rec.created_at < start:
            continue
        if end and rec.created_at and rec.created_at >= end:
            continue
        yield rec


def _parse_cast_file(path: Path) -> Optional[TerminalRecording]:
    """Parse an asciinema v2 .cast file header."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline()
            if not first_line.strip():
                return None
            header = json.loads(first_line)
    except (json.JSONDecodeError, OSError):
        return None

    # Accept asciinema v2 and v3. v3 (released 2025-08) replaced the top-level
    # "width"/"height" with a nested "term": {"cols": ..., "rows": ...}
    # block. v3 also drops the header-level "duration" field — duration is
    # only computable by reading every event row, which is too expensive for
    # the operator's archive (~3k files). We approximate from file mtime:
    # since asciinema writes events as the session runs, mtime is the last
    # event time, and (mtime - created_at) is the session duration. This is
    # an upper bound — gaps in stdout don't produce events so a session
    # that sat idle then was closed will look longer than its active span.
    if not isinstance(header, dict) or header.get("version") not in (2, "2", 3, "3"):
        return None

    env = header.get("env") or {}
    timestamp = header.get("timestamp")
    created = datetime.fromtimestamp(timestamp, tz=timezone.utc) if isinstance(timestamp, (int, float)) else None
    duration = header.get("duration")
    if duration is None and created is not None:
        # mtime fallback for v3 (and any v2 missing the field).
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            delta = (mtime - created).total_seconds()
            if delta > 0:
                duration = delta
        except OSError:
            pass

    return TerminalRecording(
        session_id=path.stem,
        path=str(path),
        created_at=created,
        duration_s=float(duration) if duration else None,
        title=header.get("title") or env.get("TITLE"),
        shell=env.get("SHELL") or header.get("command"),
    )


# ── Atuin timestamp helpers ───────────────────────────────────────────────────

def _detect_unit(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT timestamp FROM history ORDER BY timestamp DESC LIMIT 1").fetchone()
    if not row:
        return "s"
    v = int(row[0])
    if v > 10**14:
        return "ns"
    if v > 10**11:
        return "ms"
    return "s"

def _to_unit(dt: datetime, unit: str) -> int:
    ts = dt.astimezone(timezone.utc).timestamp()
    if unit == "ns":
        return int(ts * 1_000_000_000)
    if unit == "ms":
        return int(ts * 1_000)
    return int(ts)

def _from_unit(value: int, unit: str) -> datetime:
    if unit == "ns":
        seconds = value / 1_000_000_000
    elif unit == "ms":
        seconds = value / 1_000
    else:
        seconds = value
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Derived analytics
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DailyTerminalActivity:
    date: date
    command_count: int
    error_count: int
    error_rate: float
    session_count: int
    total_duration_min: float
    top_commands: tuple[str, ...]
    top_projects: tuple[str, ...]
    categories: dict[str, int]


def daily_terminal_activity(*, start: date, end: date) -> list[DailyTerminalActivity]:
    """Daily terminal usage: commands, errors, projects, command categories."""
    from collections import defaultdict

    s_dt, e_dt = date_to_dt_range(start, end)
    sessions = shell_sessions(start=s_dt, end=e_dt)
    by_day: dict[date, list[ShellSession]] = defaultdict(list)
    for s in sessions:
        by_day[s.start.date()].append(s)

    result: list[DailyTerminalActivity] = []
    for day in sorted(by_day):
        day_sessions = by_day[day]
        total_cmds = sum(s.command_count for s in day_sessions)
        total_errors = sum(s.error_count for s in day_sessions)
        total_dur = sum(s.duration_s for s in day_sessions) / 60
        cmd_counter: Counter[str] = Counter()
        proj_counter = TopN(5)
        cat_counter: Counter[str] = Counter()
        for s in day_sessions:
            for prefix in s.commands_summary:
                cmd_counter[prefix] += 1
            if s.project:
                proj_counter.add(s.project, s.duration_s)
            cat_counter[s.category] += 1
        result.append(DailyTerminalActivity(
            date=day, command_count=total_cmds, error_count=total_errors,
            error_rate=round(total_errors / max(total_cmds, 1), 3),
            session_count=len(day_sessions), total_duration_min=round(total_dur, 1),
            top_commands=tuple(c for c, _ in cmd_counter.most_common(5)),
            top_projects=tuple(p for p, _ in proj_counter.items),
            categories=dict(cat_counter),
        ))
    return result


daily_activity = daily_terminal_activity
