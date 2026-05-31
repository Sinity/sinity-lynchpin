"""Command runtime outcomes joined to machine/work state windows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime
from lynchpin.core.projects import canonical_project_name
from lynchpin.sources.terminal import AtuinCommand


@dataclass(frozen=True)
class CommandPerformanceWindow:
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    exit_code: int | None
    cwd: str | None
    project: str | None
    tool: str
    command_prefix: str
    command: str
    machine_pressure_state: str | None
    machine_work_state: str | None
    machine_overlap_seconds: float
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class CommandToolSummary:
    tool: str
    command_count: int
    error_count: int
    median_duration_seconds: float | None
    p95_duration_seconds: float | None
    pressure_overlap_count: int


@dataclass(frozen=True)
class CommandPerformanceAnalysis:
    generated_for: dict[str, Any]
    command_count: int
    tools: list[CommandToolSummary]
    windows: list[CommandPerformanceWindow]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_command_performance(
    *,
    start: date,
    end: date,
    state_path: Path | None = None,
    commands_iterable: list[AtuinCommand] | None = None,
    max_commands: int = 1000,
) -> CommandPerformanceAnalysis:
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    if commands_iterable is None:
        from lynchpin.sources.terminal import commands

        commands_iterable = list(commands(start=start_dt, end=end_dt))
    states_payload = load_json_object(
        state_path or resolve_analysis_path("machine_work_state_windows.json"),
        label="machine work-state windows",
    )
    state_rows = _state_rows(states_payload)
    caveats: list[str] = []
    if not state_rows:
        caveats.append("machine_work_state_windows.json absent or empty; command outcomes lack machine-state joins")

    windows = [_command_window(command, state_rows) for command in commands_iterable]
    windows = [window for window in windows if window is not None]
    windows.sort(key=lambda row: (row.started_at, row.cwd or "", row.command_prefix))
    if max_commands > 0 and len(windows) > max_commands:
        windows = windows[-max_commands:]
        caveats.append(f"command performance windows truncated to latest {max_commands} commands")
    return CommandPerformanceAnalysis(
        generated_for={"start": start.isoformat(), "end": end.isoformat(), "state_source": "machine_work_state_windows.json"},
        command_count=len(windows),
        tools=_tool_summaries(windows),
        windows=windows,
        caveats=sorted(dict.fromkeys(caveats)),
    )


def write_command_performance_analysis(
    out: Path,
    *,
    start: date,
    end: date,
    state_path: Path | None = None,
    max_commands: int = 1000,
) -> CommandPerformanceAnalysis:
    analysis = analyze_command_performance(start=start, end=end, state_path=state_path, max_commands=max_commands)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _command_window(command: AtuinCommand, states: list[dict[str, Any]]) -> CommandPerformanceWindow | None:
    duration = _duration_seconds(command)
    started_at = command.timestamp
    ended_at = started_at + timedelta(seconds=duration)
    state, overlap = _best_state(started_at, ended_at, states)
    return CommandPerformanceWindow(
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=round(duration, 3),
        exit_code=command.exit_code,
        cwd=command.cwd,
        project=_project_from_cwd(command.cwd),
        tool=_tool(command.command),
        command_prefix=_prefix(command.command),
        command=command.command,
        machine_pressure_state=str(state.get("pressure_state")) if state else None,
        machine_work_state=str(state.get("work_state")) if state else None,
        machine_overlap_seconds=round(overlap, 3),
        caveats=("zero or missing command duration",) if duration <= 0 else (),
    )


def _state_rows(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("windows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and _dt(row.get("started_at")) and _dt(row.get("ended_at"))]


def _best_state(started_at: datetime, ended_at: datetime, states: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_overlap = 0.0
    for row in states:
        state_start = _dt(row.get("started_at"))
        state_end = _dt(row.get("ended_at"))
        if state_start is None or state_end is None:
            continue
        overlap = max(0.0, (min(ended_at, state_end) - max(started_at, state_start)).total_seconds())
        if overlap > best_overlap:
            best = row
            best_overlap = overlap
    return best, best_overlap


def _tool_summaries(windows: list[CommandPerformanceWindow]) -> list[CommandToolSummary]:
    grouped: dict[str, list[CommandPerformanceWindow]] = defaultdict(list)
    for window in windows:
        grouped[window.tool].append(window)
    summaries = []
    for tool, rows in grouped.items():
        durations = sorted(row.duration_seconds for row in rows)
        summaries.append(
            CommandToolSummary(
                tool=tool,
                command_count=len(rows),
                error_count=sum(1 for row in rows if row.exit_code not in (None, 0)),
                median_duration_seconds=round(statistics.median(durations), 3) if durations else None,
                p95_duration_seconds=_p95(durations),
                pressure_overlap_count=sum(1 for row in rows if row.machine_pressure_state not in (None, "quiet")),
            )
        )
    return sorted(summaries, key=lambda row: (-row.command_count, row.tool))


def _duration_seconds(command: AtuinCommand) -> float:
    return max(0.0, float(command.duration_ns or 0) / 1_000_000_000)


def _tool(command: str) -> str:
    parts = _effective_parts(command)
    prefix = parts[0] if parts else "(empty)"
    if prefix in {"nix", "direnv", "pytest", "cargo", "just", "ruff", "mypy", "git", "uv"}:
        return prefix
    if prefix in {"codex", "claude", "gemini", "deepseek", "hermes"}:
        return "ai_agent"
    if prefix in {"cd", "z", "ll", "ls", "pwd", "clear", "l", "yazi", "yaz"}:
        return "navigation"
    if prefix in {"cat", "rg", "sed", "tail", "du", "df", "mv", "rm", "rmdir", "cp", "ps", "mkdir"}:
        return "shell_utility"
    if prefix in {"man", "tldr"}:
        return "docs"
    if prefix in {"nvim", "vim", "vi", "nano", "emacs"}:
        return "editor"
    if prefix in {"reboot", "shutdown", "systemctl", "journalctl"}:
        return "system"
    if prefix in {"rclone", "rsync", "scp", "sftp"}:
        return "file_transfer"
    if prefix in {"wl-paste", "wl-copy"}:
        return "clipboard"
    if prefix in {"icat"}:
        return "terminal_media"
    if prefix and all(char == "\x03" for char in prefix):
        return "control_signal"
    if prefix in {"python", "python3"}:
        return "python"
    return "other"


def _prefix(command: str) -> str:
    parts = _effective_parts(command)
    return parts[0] if parts else "(empty)"


def _effective_parts(command: str) -> list[str]:
    parts = command.strip().split()
    while parts and parts[0] == "!":
        parts = parts[1:]
    while parts and _is_env_assignment(parts[0]):
        parts = parts[1:]
    if parts and parts[0] == "sudo" and len(parts) > 1:
        parts = parts[1:]
    return parts


def _is_env_assignment(part: str) -> bool:
    if "=" not in part:
        return False
    name, _ = part.split("=", 1)
    return bool(name) and all(char == "_" or char.isalnum() for char in name) and not name[0].isdigit()


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    parts = Path(cwd).parts
    if "project" in parts:
        idx = parts.index("project")
        if idx + 1 < len(parts):
            return canonical_project_name(parts[idx + 1])
    return canonical_project_name(Path(cwd).name)


def _dt(value: object) -> datetime | None:
    return parse_datetime(value)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)
