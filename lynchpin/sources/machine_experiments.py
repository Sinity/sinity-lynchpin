"""Machine experiment manifests captured by Sinnix.

Each experiment run is a ``manifest.json`` somewhere under the host machine
capture root. Exported controlled-benchmark bundles use nested
``<run_group>/runs/<run_id>/manifest.json`` directories; ad hoc runs may use a
flat ``<run_id>/manifest.json`` directory. The manifest stores the treatment,
workload, git state, and pre/post machine state for one benchmark or stress run.
Lynchpin keeps raw JSON intact and promotes typed rows into DuckDB so experiments
can be joined to the telemetry stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config

__all__ = [
    "MachineExperimentRun",
    "experiment_root",
    "experiment_runs",
]


@dataclass(frozen=True)
class MachineExperimentRun:
    run_id: str
    run_group_id: str | None
    host: str
    workload: str
    command: tuple[str, ...]
    cwd: str | None
    started_at: datetime
    ended_at: datetime | None
    monotonic_started_ns: int | None
    monotonic_ended_ns: int | None
    exit_status: int | None
    execution_outcome: dict[str, Any]
    service_profile: str | None
    cache_profile: str | None
    measurement_context: dict[str, Any]
    planned_treatment: dict[str, Any]
    nix_internal_json_path: str | None
    git_root: str | None
    git_head: str | None
    git_branch: str | None
    git_dirty: bool | None
    pre_state: dict[str, Any]
    post_state: dict[str, Any]
    notes: tuple[str, ...]
    validation_status: str
    validation_issues: tuple[str, ...]
    validation_warnings: tuple[str, ...]
    manifest_validation: dict[str, Any]
    manifest_path: Path


def experiment_root(path: Path | None = None) -> Path:
    """Return the canonical experiment manifest root for the configured host."""
    return path or get_config().machine_host_root / "experiments"


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _within_window(started_at: datetime, start: date | None, end: date | None) -> bool:
    day = started_at.date()
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


def _read_manifest(path: Path) -> MachineExperimentRun | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("schema") == "lynchpin.machine_experiment.template.v1"
        or payload.get("template_status") is not None
    ):
        return None
    started_at = _as_utc(payload.get("started_at"))
    if started_at is None:
        return None

    git = _as_dict(payload.get("git"))
    return MachineExperimentRun(
        run_id=str(payload.get("run_id") or path.parent.name),
        run_group_id=(
            str(payload["run_group_id"])
            if payload.get("run_group_id") is not None
            else None
        ),
        host=str(payload.get("host") or ""),
        workload=str(payload.get("workload") or ""),
        command=_as_tuple(payload.get("command")),
        cwd=str(payload["cwd"]) if payload.get("cwd") is not None else None,
        started_at=started_at,
        ended_at=_as_utc(payload.get("ended_at")),
        monotonic_started_ns=_as_int(payload.get("monotonic_started_ns")),
        monotonic_ended_ns=_as_int(payload.get("monotonic_ended_ns")),
        exit_status=(
            int(payload["exit_status"])
            if payload.get("exit_status") is not None
            else None
        ),
        execution_outcome=_as_dict(payload.get("execution_outcome")),
        service_profile=(
            str(payload["service_profile"])
            if payload.get("service_profile") is not None
            else None
        ),
        cache_profile=(
            str(payload["cache_profile"])
            if payload.get("cache_profile") is not None
            else None
        ),
        measurement_context=_as_dict(payload.get("measurement_context")),
        planned_treatment=_as_dict(payload.get("planned_treatment")),
        nix_internal_json_path=(
            str(payload["nix_internal_json_path"])
            if payload.get("nix_internal_json_path") is not None
            else None
        ),
        git_root=str(git["root"]) if git.get("root") is not None else None,
        git_head=str(git["head"]) if git.get("head") is not None else None,
        git_branch=str(git["branch"]) if git.get("branch") is not None else None,
        git_dirty=bool(git["dirty"]) if git.get("dirty") is not None else None,
        pre_state=_as_dict(payload.get("pre_state")),
        post_state=_as_dict(payload.get("post_state")),
        notes=_as_tuple(payload.get("notes")),
        validation_status="unvalidated",
        validation_issues=(),
        validation_warnings=(),
        manifest_validation={},
        manifest_path=path,
    )


def experiment_runs(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
) -> Iterator[MachineExperimentRun]:
    """Yield valid machine experiment manifests ordered by start time."""
    base = experiment_root(root)
    if not base.exists():
        return
    runs: list[MachineExperimentRun] = []
    for manifest in base.rglob("manifest.json"):
        run = _read_manifest(manifest)
        if run is None or not _within_window(run.started_at, start, end):
            continue
        runs.append(run)
    for run in sorted(runs, key=lambda item: (item.started_at, item.run_id)):
        yield run
