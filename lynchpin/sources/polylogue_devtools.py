"""Polylogue repo-local devtool history source.

This intentionally reads Polylogue's primitive development tooling records, not
the Polylogue chat archive DB. It covers two repo-local ledgers:

- ``.agent/xtask/tasks.jsonl``: one row per devtools invocation.
- ``.local/logs/*.meta`` plus metrics companions: older long-run artifacts with
  process/resource samples.
"""

from __future__ import annotations

import csv
import hashlib
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from lynchpin.core.classify import resolve_project
from lynchpin.core.config import get_config


@dataclass(frozen=True)
class PolylogueDevtoolsInvocation:
    source: str
    source_id: str
    work_kind: str
    command: tuple[str, ...]
    cwd: str
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: str
    exit_code: int | None
    host: str
    project: str | None
    git_commit: str | None
    git_dirty: bool
    live_stage: str | None
    args_json: str
    cpu_usage_avg: float | None
    memory_usage_max_mb: float | None
    process_cpu_usage_avg: float | None
    process_memory_usage_max_mb: float | None
    root_process_cpu_usage_avg: float | None
    root_process_memory_usage_max_mb: float | None
    shared_nix_daemon_cpu_usage_avg: float | None
    shared_nix_daemon_memory_usage_max_mb: float | None
    shared_nix_build_slice_cpu_usage_avg: float | None
    shared_nix_build_slice_memory_usage_max_mb: float | None
    shared_background_slice_cpu_usage_avg: float | None
    shared_background_slice_memory_usage_max_mb: float | None
    host_cpu_pressure_some_avg10_max: float | None
    host_io_pressure_some_avg10_max: float | None
    host_io_pressure_full_avg10_max: float | None
    host_memory_pressure_some_avg10_max: float | None
    host_memory_pressure_full_avg10_max: float | None
    shm_free_min_mb: float | None
    shm_used_max_mb: float | None
    process_count_max: int | None
    resource_sample_count: int | None


@dataclass(frozen=True)
class PolylogueDevtoolsReadiness:
    xtask_path: Path
    logs_dir: Path
    xtask_rows: int
    meta_files: int
    first_seen: datetime | None
    last_seen: datetime | None


SOURCE = "polylogue_devtools"
WORK_KIND_XTASK = "polylogue_devtools_invocation"
WORK_KIND_LOG_RUN = "polylogue_log_run"


def polylogue_devtools_xtask_path(path: Path | None = None) -> Path:
    return path or get_config().polylogue_devtools_xtask_jsonl


def polylogue_devtools_logs_dir(path: Path | None = None) -> Path:
    return path or get_config().polylogue_devtools_logs_dir


def source_readiness(
    *,
    xtask_path: Path | None = None,
    logs_dir: Path | None = None,
) -> PolylogueDevtoolsReadiness:
    xtask = polylogue_devtools_xtask_path(xtask_path)
    logs = polylogue_devtools_logs_dir(logs_dir)
    xtask_rows = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    if xtask.exists():
        for row in _iter_jsonl_objects(xtask):
            ts = _parse_datetime(row.get("timestamp"))
            if ts is None:
                continue
            xtask_rows += 1
            first_seen = _min_dt(first_seen, ts)
            last_seen = _max_dt(last_seen, ts)
    meta_files = 0
    if logs.exists():
        for meta_path in sorted(logs.glob("*.meta")):
            meta_files += 1
            meta = _read_meta(meta_path)
            ts = _meta_started_at(meta, meta_path)
            if ts is not None:
                first_seen = _min_dt(first_seen, ts)
                last_seen = _max_dt(last_seen, ts)
    return PolylogueDevtoolsReadiness(
        xtask_path=xtask,
        logs_dir=logs,
        xtask_rows=xtask_rows,
        meta_files=meta_files,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def available(*, xtask_path: Path | None = None, logs_dir: Path | None = None) -> bool:
    ready = source_readiness(xtask_path=xtask_path, logs_dir=logs_dir)
    return ready.xtask_path.exists() or ready.logs_dir.exists()


def iter_invocations(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    xtask_path: Path | None = None,
    logs_dir: Path | None = None,
) -> Iterator[PolylogueDevtoolsInvocation]:
    yield from iter_xtask_invocations(start=start, end=end, path=xtask_path)
    yield from iter_log_invocations(start=start, end=end, logs_dir=logs_dir)


def iter_xtask_invocations(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    path: Path | None = None,
) -> Iterator[PolylogueDevtoolsInvocation]:
    target = polylogue_devtools_xtask_path(path)
    if not target.exists():
        return
    for idx, raw in enumerate(_iter_jsonl_objects(target), start=1):
        started_at = _parse_datetime(raw.get("timestamp"))
        if started_at is None or not _in_range(started_at, start=start, end=end):
            continue
        duration_s = _float_or_none(raw.get("duration_ms"))
        if duration_s is not None:
            duration_s /= 1000.0
        ended_at = started_at + timedelta(seconds=duration_s) if duration_s is not None else None
        command = tuple(str(part) for part in (raw.get("command"), *(raw.get("args") or ())) if part is not None)
        cwd = str(raw.get("cwd") or polylogue_devtools_xtask_path(path).parents[2])
        exit_code = _int_or_none(raw.get("exit_code"))
        live_stage = str(raw.get("class")) if raw.get("class") else None
        yield _invocation(
            source_id=f"polylogue:xtask:{idx}:{_stable_hash(raw)}",
            work_kind=WORK_KIND_XTASK,
            command=command,
            cwd=cwd,
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
            status=_status_from_exit(exit_code),
            exit_code=exit_code,
            git_commit=None,
            live_stage=live_stage,
            args_payload={
                "record_source": "xtask_jsonl",
                "args": raw.get("args") or [],
                "class": live_stage,
            },
        )


def iter_log_invocations(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    logs_dir: Path | None = None,
) -> Iterator[PolylogueDevtoolsInvocation]:
    root = polylogue_devtools_logs_dir(logs_dir)
    if not root.exists():
        return
    for meta_path in sorted(root.glob("*.meta")):
        meta = _read_meta(meta_path)
        started_at = _meta_started_at(meta, meta_path)
        if started_at is None or not _in_range(started_at, start=start, end=end):
            continue
        metrics_path = _resolve_companion(root, meta_path, meta.get("metrics"))
        log_path = _resolve_companion(root, meta_path, meta.get("log") or meta.get("run_log"))
        metrics = _read_metrics_summary(metrics_path)
        duration_s = metrics.get("duration_s")
        ended_at = started_at + timedelta(seconds=duration_s) if duration_s is not None else None
        command = _meta_command(meta, meta_path)
        exit_code = 1 if _looks_failed(log_path) else None
        yield _invocation(
            source_id=f"polylogue:log:{meta_path.stem}",
            work_kind=WORK_KIND_LOG_RUN,
            command=command,
            cwd=str(Path(meta.get("repo") or get_config().polylogue_project_root)),
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
            status="failed" if exit_code else "unknown",
            exit_code=exit_code,
            git_commit=meta.get("commit") or meta.get("head"),
            live_stage=meta.get("runner") or meta.get("launcher"),
            args_payload={
                "record_source": "local_logs_meta",
                "meta": {key: value for key, value in sorted(meta.items())},
                "log": str(log_path) if log_path else None,
                "metrics": str(metrics_path) if metrics_path else None,
            },
            process_cpu_usage_avg=metrics.get("process_cpu_usage_avg"),
            process_memory_usage_max_mb=metrics.get("process_memory_usage_max_mb"),
            process_count_max=_int_or_none(metrics.get("process_count_max")),
            resource_sample_count=_int_or_none(metrics.get("resource_sample_count")),
        )


def _invocation(
    *,
    source_id: str,
    work_kind: str,
    command: tuple[str, ...],
    cwd: str,
    started_at: datetime,
    ended_at: datetime | None,
    duration_s: float | None,
    status: str,
    exit_code: int | None,
    git_commit: str | None,
    live_stage: str | None,
    args_payload: dict[str, Any],
    process_cpu_usage_avg: float | None = None,
    process_memory_usage_max_mb: float | None = None,
    process_count_max: int | None = None,
    resource_sample_count: int | None = None,
) -> PolylogueDevtoolsInvocation:
    project = resolve_project(cwd, " ".join(command)) or "polylogue"
    return PolylogueDevtoolsInvocation(
        source=SOURCE,
        source_id=source_id,
        work_kind=work_kind,
        command=command,
        cwd=cwd,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        status=status,
        exit_code=exit_code,
        host=socket.gethostname(),
        project=project,
        git_commit=git_commit,
        git_dirty=False,
        live_stage=live_stage,
        args_json=json.dumps(args_payload, sort_keys=True),
        cpu_usage_avg=None,
        memory_usage_max_mb=None,
        process_cpu_usage_avg=process_cpu_usage_avg,
        process_memory_usage_max_mb=process_memory_usage_max_mb,
        root_process_cpu_usage_avg=None,
        root_process_memory_usage_max_mb=None,
        shared_nix_daemon_cpu_usage_avg=None,
        shared_nix_daemon_memory_usage_max_mb=None,
        shared_nix_build_slice_cpu_usage_avg=None,
        shared_nix_build_slice_memory_usage_max_mb=None,
        shared_background_slice_cpu_usage_avg=None,
        shared_background_slice_memory_usage_max_mb=None,
        host_cpu_pressure_some_avg10_max=None,
        host_io_pressure_some_avg10_max=None,
        host_io_pressure_full_avg10_max=None,
        host_memory_pressure_some_avg10_max=None,
        host_memory_pressure_full_avg10_max=None,
        shm_free_min_mb=None,
        shm_used_max_mb=None,
        process_count_max=process_count_max,
        resource_sample_count=resource_sample_count,
    )


def _iter_jsonl_objects(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield row


def _read_meta(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            meta[key] = value.strip()
    return meta


def _read_metrics_summary(path: Path | None) -> dict[str, float | int | None]:
    if path is None or not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except Exception:
        return {}
    elapsed: list[float] = []
    cpu: list[float] = []
    rss_kb: list[float] = []
    proc_count: list[int] = []
    for row in rows:
        if (value := _float_or_none(row.get("elapsed_s"))) is not None:
            elapsed.append(value)
        if (value := _float_or_none(row.get("cpu_pct") or row.get("cpu_percent"))) is not None:
            cpu.append(value)
        if (value := _float_or_none(row.get("rss_kb"))) is not None:
            rss_kb.append(value)
        if (value := _int_or_none(row.get("proc_count"))) is not None:
            proc_count.append(value)
    return {
        "duration_s": max(elapsed) if elapsed else None,
        "process_cpu_usage_avg": sum(cpu) / len(cpu) if cpu else None,
        "process_memory_usage_max_mb": max(rss_kb) / 1024.0 if rss_kb else None,
        "process_count_max": max(proc_count) if proc_count else None,
        "resource_sample_count": len(rows),
    }


def _resolve_companion(root: Path, meta_path: Path, raw: str | None) -> Path | None:
    candidates: list[Path] = []
    if raw:
        candidate = Path(raw)
        candidates.append(candidate if candidate.is_absolute() else root / candidate)
    for suffix in (".metrics.tsv", ".metrics", ".log"):
        candidates.append(meta_path.with_suffix(suffix))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _looks_failed(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    sample = path.read_text(encoding="utf-8", errors="replace")[-20_000:].lower()
    return any(marker in sample for marker in ("traceback", "error:", "no such file or directory"))


def _meta_command(meta: dict[str, str], meta_path: Path) -> tuple[str, ...]:
    for key in ("command", "launcher", "runner"):
        if meta.get(key):
            return tuple(part for part in meta[key].split() if part)
    return (meta_path.stem,)


def _meta_started_at(meta: dict[str, str], meta_path: Path) -> datetime | None:
    for key in ("started_at", "start_iso", "timestamp"):
        if (parsed := _parse_datetime(meta.get(key))) is not None:
            return parsed
    for part in meta_path.stem.split("-"):
        if (parsed := _parse_datetime(part)) is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y%m%dT%H%M%S%z", "%Y%m%dT%H%M%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _status_from_exit(exit_code: int | None) -> str:
    if exit_code is None:
        return "unknown"
    return "success" if exit_code == 0 else "failed"


def _in_range(value: datetime, *, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or value >= start) and (end is None or value < end)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _min_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right < left else left


def _max_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left


__all__ = [
    "PolylogueDevtoolsInvocation",
    "PolylogueDevtoolsReadiness",
    "available",
    "iter_invocations",
    "iter_log_invocations",
    "iter_xtask_invocations",
    "polylogue_devtools_logs_dir",
    "polylogue_devtools_xtask_path",
    "source_readiness",
]
