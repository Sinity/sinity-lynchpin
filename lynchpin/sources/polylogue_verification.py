"""Read-only source for Polylogue's durable verification evidence lane.

Polylogue's devtools append one canonical ``polylogue.verification-receipt``
row per verifier run (focused test, quick gate, affected or complete suite) to
a JSONL lane in the user's state directory. That lane is Polylogue's declared
export surface for verification history; the checkout-local ``.cache/verify``
run directories are disposable and are never read here.

The lane is append-only. A run can be appended more than once as its state
settles (e.g. a running run later reconciled as abandoned), so rows are keyed
by ``run_id`` and the last row wins. Rows are privacy-bounded summaries:
status, timing, source revision, selection mode, step outcomes and artifact
refs. Logs, argv and test output stay with Polylogue.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SOURCE = "polylogue_verification"
WORK_KIND = "polylogue_verification_run"
RECEIPT_KIND = "polylogue.verification-receipt"
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
PATH_ENV = "POLYLOGUE_VERIFICATION_EVIDENCE_PATH"

_TERMINAL_STATUSES = frozenset({"passed", "failed", "interrupted", "cancelled"})

#: The lane's run status in the vocabulary the other ``work_observation``
#: producers use (Sinex xtask: success/failed/running/cancelled), so one query
#: compares verification outcomes across repositories. The lane's own word is
#: kept in ``args.lane_status``.
_SHARED_STATUS = {
    "passed": "success",
    "failed": "failed",
    "interrupted": "interrupted",
    "cancelled": "cancelled",
    "unknown": "running",
}


class PolylogueVerificationError(RuntimeError):
    """Base error for the Polylogue verification lane boundary."""


class PolylogueVerificationUnavailable(PolylogueVerificationError):
    """The lane does not exist or cannot be read."""


class PolylogueVerificationContractError(PolylogueVerificationError):
    """The lane contains a row outside the supported receipt contract."""


@dataclass(frozen=True)
class PolylogueVerificationObservation:
    """One verifier run, shaped for the shared ``work_observation`` table."""

    source: str
    source_id: str
    source_revision: str
    source_generation_json: str
    artifact_refs_json: str
    caveats_json: str
    work_kind: str
    project: str
    operation: str | None
    command: tuple[str, ...]
    cwd: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: str
    exit_code: int | None
    host: str
    git_commit: str | None
    git_dirty: bool | None
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
    shm_free_min_mb: float | None = None
    shm_used_max_mb: float | None = None
    process_count_max: int | None = None
    resource_sample_count: int | None = None


@dataclass(frozen=True)
class PolylogueVerificationSnapshot:
    """The lane as read at one moment."""

    path: Path
    generation: Mapping[str, Any]
    observations: tuple[PolylogueVerificationObservation, ...]
    superseded_rows: int


def verification_lane_path(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the lane exactly as Polylogue's writer does."""
    source = os.environ if env is None else env
    configured = source.get(PATH_ENV)
    if configured:
        return Path(configured)
    state_home = source.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path(source.get("HOME", "~")).expanduser() / ".local" / "state"
    return base / "polylogue" / "verification" / "evidence.jsonl"


def read_verification_snapshot(path: Path | None = None) -> PolylogueVerificationSnapshot:
    """Read and validate the whole lane, keeping the last row per run id.

    Fails closed: any complete row that is not valid JSON or not a supported
    receipt raises :class:`PolylogueVerificationContractError`. Only a final
    line without a trailing newline -- an append still in flight -- is
    ignored, because the writer appends whole lines.
    """
    lane = path or verification_lane_path()
    try:
        stat = lane.stat()
        text = lane.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PolylogueVerificationUnavailable(f"verification lane is missing at {lane}") from error
    except OSError as error:
        raise PolylogueVerificationUnavailable(f"verification lane is unreadable at {lane}: {error}") from error

    lines = text.split("\n")
    in_flight = lines.pop()  # "" when the file ends with a newline
    by_run: dict[str, PolylogueVerificationObservation] = {}
    rows = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise PolylogueVerificationContractError(f"{lane}:{number} is not valid JSON") from error
        observation = _observation(payload, location=f"{lane}:{number}", lane=lane)
        rows += 1
        by_run[observation.source_id] = observation
    generation = {
        "interface": "polylogue.verification-evidence-lane",
        "path": str(lane),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "rows": rows,
        "in_flight_tail": bool(in_flight.strip()),
    }
    return PolylogueVerificationSnapshot(
        path=lane,
        generation=generation,
        observations=tuple(sorted(by_run.values(), key=lambda row: (row.started_at, row.source_id))),
        superseded_rows=rows - len(by_run),
    )


def iter_observations(
    *, start: datetime, end: datetime, snapshot: PolylogueVerificationSnapshot | None = None
) -> Iterator[PolylogueVerificationObservation]:
    """Yield observations whose run started in ``[start, end)``."""
    observed = snapshot or read_verification_snapshot()
    for row in observed.observations:
        if start <= row.started_at < end:
            yield row


def _observation(payload: Any, *, location: str, lane: Path) -> PolylogueVerificationObservation:
    if not isinstance(payload, Mapping):
        raise PolylogueVerificationContractError(f"{location} is not a JSON object")
    if payload.get("kind") != RECEIPT_KIND:
        raise PolylogueVerificationContractError(f"{location} has kind {payload.get('kind')!r}")
    if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise PolylogueVerificationContractError(
            f"{location} has unsupported schema_version {payload.get('schema_version')!r}"
        )
    run_id = _required_text(payload, "run_id", location)
    status = _required_text(payload, "status", location)
    semantic_status = _required_text(payload, "semantic_status", location)
    started_at = _datetime(_required_text(payload, "started_at", location), "started_at", location)
    finished = payload.get("finished_at")
    ended_at = _datetime(finished, "finished_at", location) if isinstance(finished, str) and finished else None
    duration = payload.get("duration_s")
    duration_s = float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    pytest_summary = payload.get("pytest") if isinstance(payload.get("pytest"), Mapping) else {}

    terminal = status in _TERMINAL_STATUSES
    caveats: list[str] = []
    if not terminal:
        caveats.append(f"Polylogue run status is {status}; it must not be interpreted as a result")
    if status == "interrupted":
        caveats.append("the run was interrupted before its verifier reported a result")
    if ended_at is None:
        caveats.append("finished_at is unavailable for this run")

    step_exit_codes = [step.get("exit_code") for step in steps if isinstance(step, Mapping)]
    # Only a settled run has an exit code; a running or unknown run's step codes
    # are provisional.
    exit_code = (
        next(
            (code for code in reversed(step_exit_codes) if isinstance(code, int) and not isinstance(code, bool) and code != 0),
            0 if step_exit_codes and all(code == 0 for code in step_exit_codes) else None,
        )
        if terminal
        else None
    )
    artifact_refs = [ref for ref in [payload.get("artifact_ref")] if isinstance(ref, str)]
    artifact_refs += [
        step["artifact_ref"] for step in steps if isinstance(step, Mapping) and isinstance(step.get("artifact_ref"), str)
    ]
    args = {
        "tier": _tier(run_id),
        "lane_status": status,
        "diagnosis": payload.get("diagnosis"),
        "semantic_status": semantic_status,
        "selection_mode": pytest_summary.get("selection_mode"),
        "pytest_outcomes": pytest_summary.get("outcomes"),
        "selected_count": pytest_summary.get("selected_union_count"),
        "steps": [
            {key: step.get(key) for key in ("name", "status", "diagnosis", "exit_code", "duration_s")}
            for step in steps
            if isinstance(step, Mapping)
        ],
    }
    return PolylogueVerificationObservation(
        source=SOURCE,
        source_id=f"polylogue-verification:{run_id}",
        source_revision="sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        source_generation_json=_json(
            {"interface": "polylogue.verification-evidence-lane", "path": str(lane), "schema_version": payload["schema_version"]}
        ),
        artifact_refs_json=_json(artifact_refs),
        caveats_json=_json(sorted(set(caveats))),
        work_kind=WORK_KIND,
        project="polylogue",
        operation=_tier(run_id),
        command=(),
        cwd=None,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        status=_SHARED_STATUS.get(status, status),
        exit_code=exit_code,
        host="unknown",
        git_commit=_optional_text(payload.get("source_revision")),
        git_dirty=None,
        live_stage=semantic_status,
        args_json=_json(args),
        outcome_known=True if terminal else None,
        cancellation_requested=True if status == "cancelled" else None,
        recovery_state="interrupted" if status == "interrupted" else None,
    )


def _tier(run_id: str) -> str | None:
    """``<timestamp>-<tier>-<pid>-<hex>`` -> ``<tier>``; the tier may contain hyphens."""
    parts = run_id.split("-")
    if len(parts) < 4:
        return None
    return "-".join(parts[1:-2]) or None


def _required_text(payload: Mapping[str, Any], key: str, location: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PolylogueVerificationContractError(f"{location} lacks {key}")
    return value


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime(value: Any, field: str, location: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise PolylogueVerificationContractError(f"{location} has an invalid {field}") from error
    if parsed.tzinfo is None:
        raise PolylogueVerificationContractError(f"{location} has a naive {field}")
    return parsed


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = [
    "PATH_ENV",
    "PolylogueVerificationContractError",
    "PolylogueVerificationError",
    "PolylogueVerificationObservation",
    "PolylogueVerificationSnapshot",
    "PolylogueVerificationUnavailable",
    "SOURCE",
    "WORK_KIND",
    "iter_observations",
    "read_verification_snapshot",
    "verification_lane_path",
]
