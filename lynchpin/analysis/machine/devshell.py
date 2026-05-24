"""Devshell and Nix activation performance view."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.analysis.core.io import load_json_object, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime


@dataclass(frozen=True)
class DevshellCommandWindow:
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    exit_code: int | None
    project: str | None
    command_class: str
    command: str
    machine_pressure_state: str | None
    machine_work_state: str | None
    machine_overlap_seconds: float


@dataclass(frozen=True)
class DevshellCommandSummary:
    command_class: str
    command_count: int
    error_count: int
    median_duration_seconds: float | None
    p95_duration_seconds: float | None
    pressure_overlap_count: int


@dataclass(frozen=True)
class DevshellPerformanceAnalysis:
    generated_for: dict[str, Any]
    command_count: int
    summaries: list[DevshellCommandSummary]
    windows: list[DevshellCommandWindow]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_devshell_performance(
    *,
    start: date | None = None,
    end: date | None = None,
    command_path: Path | None = None,
) -> DevshellPerformanceAnalysis:
    payload = load_json_object(
        command_path or resolve_analysis_path("command_performance_windows.json"),
        label="command performance windows",
    )
    windows: list[DevshellCommandWindow] = []
    for row in payload.get("windows", []):
        if not isinstance(row, dict) or _command_class(str(row.get("command") or "")) is None:
            continue
        window = _devshell_window(row)
        if window is not None:
            windows.append(window)
    windows.sort(key=lambda row: (row.started_at, row.command_class, row.command))
    caveats = list(payload.get("caveats") or [])
    if not windows:
        caveats.append("no direnv/nix devshell commands found in command performance artifact")
    caveats.append("classification is command-text based; Nix phase attribution requires structured Nix logs")
    return DevshellPerformanceAnalysis(
        generated_for=_generated_for(start, end),
        command_count=len(windows),
        summaries=_summaries(windows),
        windows=windows,
        caveats=sorted(dict.fromkeys(str(caveat) for caveat in caveats if caveat)),
    )


def write_devshell_performance_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    command_path: Path | None = None,
) -> DevshellPerformanceAnalysis:
    analysis = analyze_devshell_performance(start=start, end=end, command_path=command_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _devshell_window(row: dict[str, Any]) -> DevshellCommandWindow | None:
    command = str(row.get("command") or "")
    command_class = _command_class(command)
    if command_class is None:
        return None
    started_at = _dt(row.get("started_at"))
    ended_at = _dt(row.get("ended_at"))
    if started_at is None or ended_at is None:
        return None
    return DevshellCommandWindow(
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=_float(row.get("duration_seconds")),
        exit_code=int(row["exit_code"]) if isinstance(row.get("exit_code"), int) else None,
        project=str(row.get("project")) if row.get("project") else None,
        command_class=command_class,
        command=command,
        machine_pressure_state=str(row.get("machine_pressure_state")) if row.get("machine_pressure_state") else None,
        machine_work_state=str(row.get("machine_work_state")) if row.get("machine_work_state") else None,
        machine_overlap_seconds=_float(row.get("machine_overlap_seconds")),
    )


def _command_class(command: str) -> str | None:
    parts = _effective_parts(command)
    if not parts:
        return None
    command_name = parts[0]
    subcommand = parts[1] if len(parts) > 1 else ""
    if command_name == "direnv" and subcommand in {"reload", "allow", "exec"}:
        return "direnv_activation"
    if command_name == "nix" and subcommand == "develop":
        return "nix_develop"
    if command_name == "nix" and subcommand == "build":
        return "nix_build"
    if command_name == "nix" and subcommand == "flake":
        return "nix_flake"
    if command_name == "nix":
        return "nix_other"
    return None


def _effective_parts(command: str) -> list[str]:
    parts = command.strip().lower().split()
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


def _summaries(windows: list[DevshellCommandWindow]) -> list[DevshellCommandSummary]:
    grouped: dict[str, list[DevshellCommandWindow]] = defaultdict(list)
    for window in windows:
        grouped[window.command_class].append(window)
    summaries: list[DevshellCommandSummary] = []
    for command_class, rows in grouped.items():
        durations = sorted(row.duration_seconds for row in rows)
        summaries.append(
            DevshellCommandSummary(
                command_class=command_class,
                command_count=len(rows),
                error_count=sum(1 for row in rows if row.exit_code not in (None, 0)),
                median_duration_seconds=round(statistics.median(durations), 3) if durations else None,
                p95_duration_seconds=_p95(durations),
                pressure_overlap_count=sum(1 for row in rows if row.machine_pressure_state not in (None, "quiet")),
            )
        )
    return sorted(summaries, key=lambda row: (-row.command_count, row.command_class))


def _dt(value: object) -> datetime | None:
    return parse_datetime(value)


def _float(value: object) -> float:
    try:
        return float(str(value or 0.0))
    except ValueError:
        return 0.0


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)


def _generated_for(start: date | None, end: date | None) -> dict[str, Any]:
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "source": "command_performance_windows.json",
    }
