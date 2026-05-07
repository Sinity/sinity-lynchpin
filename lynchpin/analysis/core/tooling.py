"""Third-party tool execution with structured capture.

Every tool invocation records binary, version, command, cwd, return code,
duration, output size, and stderr sample — making missing tools explicit
instead of silently degrading analysis.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    binary: str
    version_args: tuple[str, ...] = ("--version",)
    optional: bool = True


@dataclass(frozen=True)
class ToolRun:
    name: str
    binary_path: str | None
    available: bool
    version: str | None
    command: tuple[str, ...]
    cwd: str
    returncode: int | None
    duration_s: float
    stdout_bytes: int
    stderr_sample: str
    generated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def find_tool(spec: ToolSpec) -> str | None:
    return shutil.which(spec.binary)


def check_tool(spec: ToolSpec) -> ToolRun:
    binary_path = find_tool(spec)
    if binary_path is None:
        return ToolRun(
            name=spec.name, binary_path=None, available=False, version=None,
            command=(), cwd="", returncode=None, duration_s=0.0, stdout_bytes=0,
            stderr_sample="binary not found on PATH",
        )
    try:
        result = subprocess.run(
            [binary_path, *spec.version_args], capture_output=True, text=True, timeout=15,
        )
        version = (result.stdout.strip().split("\n")[0] or result.stderr.strip().split("\n")[0])[:200]
        return ToolRun(
            name=spec.name, binary_path=binary_path, available=True, version=version,
            command=(binary_path, *spec.version_args), cwd="",
            returncode=result.returncode, duration_s=0.0,
            stdout_bytes=len(result.stdout.encode()),
            stderr_sample=result.stderr[:500] if result.returncode != 0 else "",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ToolRun(
            name=spec.name, binary_path=binary_path, available=False, version=None,
            command=(), cwd="", returncode=None, duration_s=0.0, stdout_bytes=0,
            stderr_sample=f"{type(exc).__name__}: {exc}",
        )


def run_tool(
    spec: ToolSpec, args: Sequence[str], *,
    cwd: str = "", timeout_s: float = 120.0,
    max_output_bytes: int = 50 * 1024 * 1024,
) -> ToolRun:
    binary_path = find_tool(spec)
    if binary_path is None:
        return ToolRun(
            name=spec.name, binary_path=None, available=False, version=None,
            command=tuple(args), cwd=cwd, returncode=None, duration_s=0.0,
            stdout_bytes=0, stderr_sample="binary not found on PATH",
        )
    start = time.monotonic()
    try:
        result = subprocess.run(
            [binary_path, *args], capture_output=True, text=True,
            timeout=timeout_s, cwd=cwd or None,
        )
        duration_s = time.monotonic() - start
        stdout_bytes = len(result.stdout.encode())
        if stdout_bytes > max_output_bytes:
            result.stdout = result.stdout[:max_output_bytes]
        return ToolRun(
            name=spec.name, binary_path=binary_path, available=True,
            version=_cached_version(spec),
            command=(binary_path, *args), cwd=cwd,
            returncode=result.returncode, duration_s=round(duration_s, 3),
            stdout_bytes=stdout_bytes,
            stderr_sample=result.stderr[:500] if result.returncode != 0 else "",
        )
    except subprocess.TimeoutExpired:
        return ToolRun(
            name=spec.name, binary_path=binary_path, available=True,
            version=_cached_version(spec), command=(binary_path, *args), cwd=cwd,
            returncode=None, duration_s=round(time.monotonic() - start, 3),
            stdout_bytes=0, stderr_sample="timeout expired",
        )
    except OSError as exc:
        return ToolRun(
            name=spec.name, binary_path=binary_path, available=False, version=None,
            command=(binary_path, *args), cwd=cwd, returncode=None,
            duration_s=round(time.monotonic() - start, 3), stdout_bytes=0,
            stderr_sample=f"{type(exc).__name__}: {exc}",
        )


def run_tool_json(
    spec: ToolSpec, args: Sequence[str], *,
    cwd: str = "", timeout_s: float = 120.0,
) -> tuple[ToolRun, object | None]:
    run = run_tool(spec, args, cwd=cwd, timeout_s=timeout_s)
    if run.returncode != 0 or not run.available:
        return run, None
    try:
        result = subprocess.run(
            [run.binary_path, *args], capture_output=True, text=True,
            timeout=timeout_s, cwd=cwd or None,
        )
        return run, json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
        return ToolRun(
            name=run.name, binary_path=run.binary_path, available=run.available,
            version=run.version, command=run.command, cwd=run.cwd,
            returncode=-1, duration_s=run.duration_s, stdout_bytes=run.stdout_bytes,
            stderr_sample=f"json parse failed: {type(exc).__name__}: {exc}",
        ), None


def active_tool_inventory(specs: Sequence[ToolSpec]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for spec in specs:
        run = check_tool(spec)
        runs.append({
            "name": run.name, "binary_path": run.binary_path,
            "available": run.available, "version": run.version,
            "returncode": run.returncode,
            "stderr_sample": run.stderr_sample if not run.available else "",
        })
    available = [r["name"] for r in runs if r["available"]]
    unavailable = [r["name"] for r in runs if not r["available"]]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tool_count": len(runs), "available_count": len(available),
        "unavailable_count": len(unavailable),
        "available": available, "unavailable": unavailable, "tools": runs,
    }


_VERSION_CACHE: dict[str, str] = {}


def _cached_version(spec: ToolSpec) -> str | None:
    if spec.name in _VERSION_CACHE:
        return _VERSION_CACHE[spec.name]
    try:
        binary = shutil.which(spec.binary)
        if binary is None:
            return None
        result = subprocess.run(
            [binary, *spec.version_args], capture_output=True, text=True, timeout=15,
        )
        version = (result.stdout.strip().split("\n")[0] or result.stderr.strip().split("\n")[0])[:200]
        _VERSION_CACHE[spec.name] = version
        return version
    except (subprocess.TimeoutExpired, OSError):
        return None


__all__ = [
    "ToolSpec", "ToolRun", "active_tool_inventory", "check_tool",
    "find_tool", "run_tool", "run_tool_json",
]
