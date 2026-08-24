"""Read-only AgentCTL job-observation source.

Only the versioned ``agentctl job list`` public envelope is consumed here.  In
particular, this module does not inspect launch inputs, job storage, logs, or
result payloads.  Artifact and semantic values stay opaque references.
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
CONTRACT_SCHEMA = 1
_COMMAND = ("agentctl", "job", "list")
_SEMANTIC_OWNERS = frozenset({"polylogue", "sinex"})


class AgentctlObservationError(RuntimeError):
    """Base error for the AgentCTL observation boundary."""


class AgentctlObservationUnavailable(AgentctlObservationError):
    """The public read route could not be reached."""


class AgentctlObservationContractError(AgentctlObservationError):
    """The public route did not satisfy the supported versioned contract."""


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
    """One coherent page from the public AgentCTL observation route."""

    contract_schema: int
    generation: Mapping[str, Any]
    observations: tuple[AgentctlJobObservation, ...]
    caveats: tuple[str, ...]


def read_observation_snapshot(
    *,
    loader: Callable[[], Mapping[str, Any]] | None = None,
) -> AgentctlObservationSnapshot:
    """Read and validate the supported public AgentCTL observation envelope."""
    envelope = dict((loader or _load_public_envelope)())
    if envelope.get("schema") != CONTRACT_SCHEMA:
        raise AgentctlObservationContractError(
            "agentctl job.list requires public envelope schema 1"
        )
    if envelope.get("ok") is not True:
        raise AgentctlObservationContractError("agentctl job.list returned an unsuccessful envelope")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping) or payload.get("kind") != "inline":
        raise AgentctlObservationContractError("agentctl job.list requires an inline public payload")
    value = payload.get("value")
    if not isinstance(value, Mapping) or not isinstance(value.get("jobs"), list):
        raise AgentctlObservationContractError("agentctl job.list payload is missing jobs")

    snapshot = value.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise AgentctlObservationContractError("agentctl job.list payload is missing snapshot provenance")
    generation = {
        "interface": "agentctl.job.list",
        "contract_schema": CONTRACT_SCHEMA,
        "snapshot": _safe_snapshot(snapshot),
    }
    caveats: list[str] = [
        "AgentCTL job-list snapshot: lifecycle state is current durable observation, not a complete event history",
        "AgentCTL v1 does not publish a host identity, restart/recovery marker, or semantic receipt refs unless explicitly added to a job record",
        "AgentCTL created_at is durable job-record creation time, not a confirmed process-start timestamp",
        "AgentCTL state.observed_at is reconciliation time, not a terminal completion timestamp",
    ]
    if value.get("truncated") is True:
        caveats.append("AgentCTL job-list snapshot is truncated; missing rows are not zero activity")
    observations: list[AgentctlJobObservation] = []
    for job in value["jobs"]:
        if not isinstance(job, Mapping):
            raise AgentctlObservationContractError("agentctl job.list contains an invalid job record")
        observations.append(
            _job_observation(job, generation=generation, snapshot_caveats=caveats)
        )
    return AgentctlObservationSnapshot(
        contract_schema=CONTRACT_SCHEMA,
        generation=generation,
        observations=tuple(observations),
        caveats=tuple(caveats),
    )


def _load_public_envelope() -> Mapping[str, Any]:
    try:
        result = subprocess.run(
            _COMMAND,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentctlObservationUnavailable("agentctl job.list public route is unavailable") from error
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AgentctlObservationContractError("agentctl job.list returned invalid JSON") from error
    if not isinstance(value, Mapping):
        raise AgentctlObservationContractError("agentctl job.list returned a non-object envelope")
    return value


def _job_observation(
    job: Mapping[str, Any],
    *,
    generation: Mapping[str, Any],
    snapshot_caveats: list[str],
) -> AgentctlJobObservation:
    job_id = _required_text(job, "job_id")
    state = _mapping(job.get("state"), "state")
    phase = _required_text(state, "phase")
    terminal = _required_bool(state, "terminal")
    created_at = _parse_datetime(_required_text(job, "created_at"), "created_at")
    _parse_datetime(_required_text(state, "observed_at"), "state.observed_at")
    systemd = state.get("systemd")
    systemd_state = systemd if isinstance(systemd, Mapping) else {}
    artifacts = job.get("artifacts")
    artifact_map = artifacts if isinstance(artifacts, Mapping) else {}
    artifact_refs = _artifact_refs(artifact_map)
    receipt_refs = _receipt_refs(job.get("semantic_receipts"))
    recovery_state = _optional_recovery_state(state.get("recovery"))
    cancellation_requested = True if phase == "cancelled" else _optional_bool(state.get("cancellation_requested"))
    outcome_known = _outcome_known(phase=phase, terminal=terminal)
    caveats = list(snapshot_caveats)
    if not artifact_refs:
        caveats.append("AgentCTL record exposes no artifact references")
    elif artifact_map.get("result") is None:
        caveats.append("AgentCTL result artifact is absent; no semantic result was read")
    if outcome_known is not True:
        caveats.append(f"AgentCTL outcome is {phase}; it must not be interpreted as success")
    if phase == "cancelled" or cancellation_requested is True:
        caveats.append("AgentCTL cancellation is recorded as lifecycle evidence, not a successful result")
    if recovery_state is None:
        caveats.append("AgentCTL v1 does not expose whether this observation followed daemon recovery or restart")
    if not receipt_refs:
        caveats.append("No explicit Polylogue or Sinex semantic receipt refs were published; logs and result artifacts were not inspected")

    safe_record = {
        "job_id": job_id,
        "kind": _optional_text(job.get("kind")),
        "project_id": _optional_text(job.get("project_id")),
        "operation": _optional_text(job.get("operation")),
        "created_at": created_at.isoformat(),
        "timeout_seconds": _optional_int(job.get("timeout_seconds")),
        "state": _safe_state(state),
        "artifact_refs": artifact_refs,
        "semantic_receipts": [(ref.owner, ref.ref) for ref in receipt_refs],
    }
    source_revision = "sha256:" + hashlib.sha256(
        json.dumps(safe_record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    exit_code = _terminal_exit_code(
        systemd_state,
        phase=phase,
        terminal=terminal,
        outcome_known=outcome_known,
    )
    memory_peak = _memory_peak_mib(systemd_state.get("MemoryPeak"))
    return AgentctlJobObservation(
        source=SOURCE,
        source_id=f"agentctl:{job_id}",
        source_revision=source_revision,
        source_generation_json=_json(generation),
        artifact_refs_json=_json(artifact_refs),
        caveats_json=_json(sorted(set(caveats))),
        receipt_refs=receipt_refs,
        work_kind=WORK_KIND,
        project=_optional_text(job.get("project_id")),
        command=(),
        cwd=None,
        started_at=created_at,
        ended_at=None,
        duration_s=None,
        status=phase,
        exit_code=exit_code,
        host="unknown",
        git_commit=None,
        git_dirty=False,
        live_stage=phase,
        args_json="{}",
        outcome_known=outcome_known,
        cancellation_requested=cancellation_requested,
        recovery_state=recovery_state,
        memory_usage_max_mb=memory_peak,
        process_memory_usage_max_mb=memory_peak,
        resource_sample_count=1 if memory_peak is not None else None,
    )


def _safe_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ordering": _optional_text(snapshot.get("ordering")),
        "ceiling": list(snapshot.get("ceiling") or ()) if isinstance(snapshot.get("ceiling"), list) else [],
    }


def _safe_state(state: Mapping[str, Any]) -> dict[str, Any]:
    systemd = state.get("systemd")
    systemd_state = systemd if isinstance(systemd, Mapping) else {}
    return {
        "phase": _optional_text(state.get("phase")),
        "terminal": _optional_bool(state.get("terminal")),
        "observed_at": _optional_text(state.get("observed_at")),
        "result_evidence": _optional_text(state.get("result_evidence")),
        "cancellation_requested": _optional_bool(state.get("cancellation_requested")),
        "recovery": _optional_text(state.get("recovery")),
        "systemd": {
            key: _optional_text(systemd_state.get(key))
            for key in ("ActiveState", "SubState", "Result", "ExecMainCode", "ExecMainStatus", "MemoryPeak")
        },
    }


def _artifact_refs(artifacts: Mapping[str, Any]) -> list[str]:
    refs: set[str] = set()
    for name in ("log", "result"):
        artifact = artifacts.get(name)
        if isinstance(artifact, Mapping) and isinstance(artifact.get("ref"), str):
            refs.add(str(artifact["ref"]))
    return sorted(refs)


def _receipt_refs(value: Any) -> tuple[AgentctlReceiptRef, ...]:
    if not isinstance(value, list):
        return ()
    refs: set[AgentctlReceiptRef] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        owner = entry.get("owner")
        ref = entry.get("ref")
        if isinstance(owner, str) and owner in _SEMANTIC_OWNERS and isinstance(ref, str) and ref:
            refs.add(AgentctlReceiptRef(owner=owner, ref=ref))
    return tuple(sorted(refs, key=lambda item: (item.owner, item.ref)))


def _outcome_known(*, phase: str, terminal: bool) -> bool | None:
    if phase in {"observation-unknown", "outcome-unknown", "launch-unknown"}:
        return None
    if not terminal:
        return None
    if phase in {"succeeded", "failed", "cancelled", "missing", "launch-failed"}:
        return True
    return None


def _terminal_exit_code(
    systemd: Mapping[str, Any],
    *,
    phase: str,
    terminal: bool,
    outcome_known: bool | None,
) -> int | None:
    if not terminal or outcome_known is not True or phase not in {"succeeded", "failed", "cancelled"}:
        return None
    return _systemd_int(systemd.get("ExecMainStatus"))


def _memory_peak_mib(value: Any) -> float | None:
    if not isinstance(value, str) or not value.isdigit():
        return None
    peak = int(value)
    return peak / (1024 * 1024) if peak > 0 else None


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}") from error
    if parsed.tzinfo is None:
        raise AgentctlObservationContractError(f"agentctl job.list has timezone-naive {field}")
    return parsed.astimezone(timezone.utc)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentctlObservationContractError(f"agentctl job.list has invalid {field}")
    return value


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


def _systemd_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_recovery_state(value: Any) -> str | None:
    return value if isinstance(value, str) and value in {"recovered", "restarted"} else None


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
