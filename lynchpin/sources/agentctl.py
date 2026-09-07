"""Read-only AgentCTL job-observation source.

Only the native JSON rows from ``agentctl job list --all`` are consumed here.
This module does not inspect launch inputs, job storage, logs, or result
payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import subprocess
from typing import Any


SOURCE = "agentctl"
WORK_KIND = "agentctl_job"
CONTRACT_SCHEMA = 2
_COMMAND = ("agentctl", "job", "list", "--json", "--all")


class AgentctlObservationError(RuntimeError):
    """Base error for the AgentCTL observation boundary."""


class AgentctlObservationUnavailable(AgentctlObservationError):
    """The public read route could not be reached."""


class AgentctlObservationContractError(AgentctlObservationError):
    """The public route did not satisfy the supported native contract."""


@dataclass(frozen=True)
class AgentctlReceiptRef:
    """An optional semantic receipt explicitly published by AgentCTL."""

    owner: str
    ref: str


@dataclass(frozen=True)
class AgentctlJobObservation:
    """A privacy-bounded lifecycle observation for one durable AgentCTL job."""

    source: str
    source_id: str
    source_revision: str
    source_generation_json: str
    artifact_refs_json: str
    caveats_json: str
    receipt_refs: tuple[AgentctlReceiptRef, ...]
    work_kind: str
    project: str | None
    command: tuple[str, ...]
    cwd: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: str
    exit_code: int | None
    host: str
    git_commit: str | None
    git_dirty: bool
    live_stage: str | None
    args_json: str
    outcome_known: bool | None
    cancellation_requested: bool | None
    recovery_state: str | None
    cpu_usage_avg: float | None = None
    memory_usage_max_mb: float | None = None
    process_cpu_usage_avg: float | None = None
    process_memory_usage_max_mb: float | None = None
    root_process_cpu_usage_avg: float | None = None
    root_process_memory_usage_max_mb: float | None = None
    shared_nix_daemon_cpu_usage_avg: float | None = None
    shared_nix_daemon_memory_usage_max_mb: float | None = None
    shared_nix_build_slice_cpu_usage_avg: float | None = None
    shared_nix_build_slice_memory_usage_max_mb: float | None = None
    shared_background_slice_cpu_usage_avg: float | None = None
    shared_background_slice_memory_usage_max_mb: float | None = None
    host_cpu_pressure_some_avg10_max: float | None = None
    host_io_pressure_some_avg10_max: float | None = None
    host_io_pressure_full_avg10_max: float | None = None
    host_memory_pressure_some_avg10_max: float | None = None
    host_memory_pressure_full_avg10_max: float | None = None
    host_block_read_mib_delta: float | None = None
    host_block_write_mib_delta: float | None = None
    host_block_read_iops_avg: float | None = None
    host_block_write_iops_avg: float | None = None
    host_block_busiest_device: str | None = None
    host_block_busiest_device_total_mib_delta: float | None = None
    host_block_busiest_device_read_iops_avg: float | None = None
    host_block_busiest_device_write_iops_avg: float | None = None
    host_block_busiest_device_weighted_io_ms_per_s: float | None = None
    shm_free_min_mb: float | None = None
    shm_used_max_mb: float | None = None
    process_count_max: int | None = None
    resource_sample_count: int | None = None


@dataclass(frozen=True)
class AgentctlObservationSnapshot:
    """One response from the public AgentCTL observation route."""

    contract_schema: int
    generation: Mapping[str, Any]
    observations: tuple[AgentctlJobObservation, ...]
    caveats: tuple[str, ...]


def read_observation_snapshot(
    *, loader: Callable[[], Any] | None = None,
) -> AgentctlObservationSnapshot:
    """Read and validate native public AgentCTL job-list rows."""
    rows = (loader or _load_public_rows)()
    if not isinstance(rows, list):
        raise AgentctlObservationContractError(
            "agentctl job.list --json --all returned a non-list response"
        )
    generation = {
        "interface": "agentctl.job.list",
        "contract_schema": CONTRACT_SCHEMA,
        "command": list(_COMMAND),
        "coverage": "all",
    }
    caveats: list[str] = [
        "AgentCTL job-list output is a live durable-state response, not a complete event history",
        "AgentCTL job-list output does not publish a snapshot identity, host identity, restart/recovery marker, artifact refs, or semantic receipt refs",
    ]
    observations: list[AgentctlJobObservation] = []
    for job in rows:
        if not isinstance(job, Mapping):
            raise AgentctlObservationContractError("agentctl job.list contains an invalid job record")
        observations.append(_job_observation(job, generation=generation, snapshot_caveats=caveats))
    return AgentctlObservationSnapshot(
        contract_schema=CONTRACT_SCHEMA,
        generation=generation,
        observations=tuple(observations),
        caveats=tuple(caveats),
    )


def _load_public_rows() -> Any:
    try:
        result = subprocess.run(
            _COMMAND,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentctlObservationUnavailable(
            "agentctl job.list --json --all route is unavailable"
        ) from error
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AgentctlObservationContractError(
            "agentctl job.list --json --all returned invalid JSON"
        ) from error


def _job_observation(
    job: Mapping[str, Any],
    *,
    generation: Mapping[str, Any],
    snapshot_caveats: list[str],
) -> AgentctlJobObservation:
    job_id = _required_job_id(job, "job_id")
    phase = _required_text(job, "phase")
    terminal = _required_bool(job, "terminal")
    enqueued_at = _parse_datetime(_required_text(job, "enqueued_at"), "enqueued_at")
    published_started_at = _optional_datetime(job.get("started_at"), "started_at")
    ended_at = _optional_datetime(job.get("ended_at"), "ended_at")
    outcome_known = _outcome_known(phase=phase, terminal=terminal)
    caveats = list(snapshot_caveats)
    started_at = published_started_at
    if started_at is None:
        started_at = enqueued_at
        caveats.append(
            "AgentCTL started_at is unavailable; enqueued_at is used as the observation timestamp"
        )
    if outcome_known is not True:
        caveats.append(f"AgentCTL outcome is {phase}; it must not be interpreted as success")
    if phase == "cancelled":
        caveats.append("AgentCTL cancellation is recorded as lifecycle evidence, not a successful result")
    if ended_at is None and terminal:
        caveats.append("AgentCTL ended_at is unavailable for this terminal observation")

    safe_record = {
        "job_id": job_id,
        "kind": _optional_text(job.get("kind")),
        "project": _optional_text(job.get("project")),
        "operation": _optional_text(job.get("operation")),
        "group": _optional_text(job.get("group")),
        "phase": phase,
        "terminal": terminal,
        "result": _optional_text(job.get("result")),
        "exit_code": _optional_int(job.get("exit_code")),
        "enqueued_at": enqueued_at.isoformat(),
        "started_at": started_at.isoformat() if started_at is not None else None,
        "ended_at": ended_at.isoformat() if ended_at is not None else None,
    }
    source_revision = "sha256:" + hashlib.sha256(
        json.dumps(safe_record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    exit_code = _terminal_exit_code(job, terminal=terminal, outcome_known=outcome_known)
    duration_s = (
        (ended_at - published_started_at).total_seconds()
        if ended_at is not None and published_started_at is not None
        else None
    )
    return AgentctlJobObservation(
        source=SOURCE,
        source_id=f"agentctl:{job_id}",
        source_revision=source_revision,
        source_generation_json=_json(generation),
        artifact_refs_json="[]",
        caveats_json=_json(sorted(set(caveats))),
        receipt_refs=(),
        work_kind=WORK_KIND,
        project=_optional_text(job.get("project")),
        command=(),
        cwd=None,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        status=phase,
        exit_code=exit_code,
        host="unknown",
        git_commit=None,
        git_dirty=False,
        live_stage=phase,
        args_json="{}",
        outcome_known=outcome_known,
        cancellation_requested=True if phase == "cancelled" else None,
        recovery_state=None,
    )


def _outcome_known(*, phase: str, terminal: bool) -> bool | None:
    if phase in {"observation-unknown", "outcome-unknown", "launch-unknown"}:
        return None
    if not terminal:
        return None
    if phase in {
        "succeeded",
        "failed",
        "cancelled",
        "dependency-failed",
        "launch-failed",
        "timeout",
        "refused",
        "vanished",
        "slot-occupied",
    }:
        return True
    return None


def _terminal_exit_code(
    job: Mapping[str, Any],
    *,
    terminal: bool,
    outcome_known: bool | None,
) -> int | None:
    if not terminal or outcome_known is not True:
        return None
    return _optional_int(job.get("exit_code"))


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}") from error
    if parsed.tzinfo is None:
        raise AgentctlObservationContractError(f"agentctl job.list has timezone-naive {field}")
    return parsed.astimezone(timezone.utc)


def _optional_datetime(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}")
    return _parse_datetime(value, field)


def _required_job_id(value: Mapping[str, Any], field: str) -> str:
    candidate = value.get(field)
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return str(candidate)
    if isinstance(candidate, str) and candidate:
        return candidate
    raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}")


def _required_text(value: Mapping[str, Any], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not candidate:
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}")
    return candidate


def _required_bool(value: Mapping[str, Any], field: str) -> bool:
    candidate = value.get(field)
    if not isinstance(candidate, bool):
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}")
    return candidate


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = [
    "AgentctlJobObservation",
    "AgentctlObservationContractError",
    "AgentctlObservationError",
    "AgentctlObservationSnapshot",
    "AgentctlObservationUnavailable",
    "AgentctlReceiptRef",
    "CONTRACT_SCHEMA",
    "SOURCE",
    "read_observation_snapshot",
]
