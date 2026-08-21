"""Low-overhead phase telemetry for evidence-graph maintenance."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import resource
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class PerformanceSample:
    """Process resource counters captured at one graph phase boundary."""

    monotonic_seconds: float
    cpu_seconds: float
    read_bytes: int | None
    write_bytes: int | None


def sample_performance() -> PerformanceSample:
    """Capture elapsed-time, CPU, and Linux process I/O counters."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    read_bytes, write_bytes = _process_io_bytes()
    return PerformanceSample(
        monotonic_seconds=perf_counter(),
        cpu_seconds=usage.ru_utime + usage.ru_stime,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
    )


def log_performance(
    logger: logging.Logger,
    *,
    component: str,
    stage: str,
    started: PerformanceSample,
    **fields: Any,
) -> None:
    """Emit a parseable resource delta for one completed graph phase."""
    finished = sample_performance()
    elapsed_seconds = max(0.0, finished.monotonic_seconds - started.monotonic_seconds)
    cpu_seconds = max(0.0, finished.cpu_seconds - started.cpu_seconds)
    payload: dict[str, Any] = {
        "component": component,
        "stage": stage,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "cpu_seconds": round(cpu_seconds, 6),
        "average_cpu_cores": round(cpu_seconds / elapsed_seconds, 6)
        if elapsed_seconds
        else 0.0,
        **fields,
    }
    if started.read_bytes is not None and finished.read_bytes is not None:
        payload["read_bytes"] = max(0, finished.read_bytes - started.read_bytes)
    if started.write_bytes is not None and finished.write_bytes is not None:
        payload["write_bytes"] = max(0, finished.write_bytes - started.write_bytes)
    logger.info("evidence_graph_performance %s", json.dumps(payload, sort_keys=True))


def _process_io_bytes() -> tuple[int | None, int | None]:
    """Return process physical I/O when Linux exposes it through procfs."""
    try:
        values = {
            key: int(value)
            for line in Path("/proc/self/io").read_text().splitlines()
            if ":" in line
            for key, value in [line.split(":", maxsplit=1)]
        }
    except (OSError, ValueError):
        return None, None
    return values.get("read_bytes"), values.get("write_bytes")
