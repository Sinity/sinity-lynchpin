"""Low-overhead phase telemetry for evidence-graph maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import resource
import threading
from time import perf_counter
from typing import Any, Callable
from contextlib import contextmanager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerformanceSample:
    """Process resource counters captured at one graph phase boundary."""

    monotonic_seconds: float
    cpu_seconds: float
    read_bytes: int | None
    write_bytes: int | None


@dataclass(frozen=True)
class StageToken:
    stage_id: str
    stage: str
    started_at: str
    window_start: str
    window_end: str
    node_count: int
    edge_count: int


class GraphStageRecorder:
    """Append-only durable lifecycle records for one graph attempt."""

    def __init__(
        self,
        *,
        attempt_id: str,
        refresh_id: str,
        path: Path | None = None,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.attempt_id = attempt_id
        self.refresh_id = refresh_id
        self.path = path
        self.sink = sink
        self._lock = threading.Lock()
        self.records: list[dict[str, Any]] = []

    @classmethod
    def for_window(
        cls,
        *,
        start: date,
        end: date,
        attempt_id: str | None = None,
        refresh_id: str | None = None,
        path: Path | None = None,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> "GraphStageRecorder":
        generated = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return cls(
            attempt_id=attempt_id or f"graph-attempt:{generated}",
            refresh_id=refresh_id or f"graph:{start.isoformat()}:{end.isoformat()}:all",
            path=path,
            sink=sink,
        )

    def start(
        self,
        stage: str,
        *,
        window_start: date | str,
        window_end: date | str,
        node_count: int,
        edge_count: int,
    ) -> tuple[StageToken, PerformanceSample]:
        sample = sample_performance()
        token = StageToken(
            stage_id=f"{self.attempt_id}:{stage}:{len(self.records) // 2}",
            stage=stage,
            started_at=datetime.now(timezone.utc).isoformat(),
            window_start=str(window_start),
            window_end=str(window_end),
            node_count=node_count,
            edge_count=edge_count,
        )
        self._write(
            self._payload(token, status="started", sample=sample, node_count=node_count, edge_count=edge_count)
        )
        return token, sample

    def finish(
        self,
        token: StageToken,
        started: PerformanceSample,
        *,
        status: str,
        node_count: int,
        edge_count: int,
        caveat: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        finished = sample_performance()
        payload = self._payload(
            token,
            status=status,
            sample=finished,
            node_count=node_count,
            edge_count=edge_count,
            started=started,
        )
        payload["node_delta"] = node_count - token.node_count
        payload["edge_delta"] = edge_count - token.edge_count
        if caveat:
            payload["caveat"] = caveat
        if error is not None:
            payload["error"] = f"{type(error).__name__}: {error}"
        self._write(payload)

    def _payload(
        self,
        token: StageToken,
        *,
        status: str,
        sample: PerformanceSample,
        node_count: int,
        edge_count: int,
        started: PerformanceSample | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "lynchpin.graph-stage-receipt.v1",
            "event": status,
            "status": status,
            "stage_id": token.stage_id,
            "stage": token.stage,
            "attempt_id": self.attempt_id,
            "refresh_id": self.refresh_id,
            "window_start": token.window_start,
            "window_end": token.window_end,
            "started_at": token.started_at,
            "ended_at": datetime.now(timezone.utc).isoformat() if started is not None else None,
            "node_count": node_count,
            "edge_count": edge_count,
        }
        if started is not None:
            elapsed = max(0.0, sample.monotonic_seconds - started.monotonic_seconds)
            cpu = max(0.0, sample.cpu_seconds - started.cpu_seconds)
            payload.update(
                elapsed_seconds=round(elapsed, 6),
                cpu_seconds=round(cpu, 6),
                read_bytes=_counter_delta(started.read_bytes, sample.read_bytes),
                write_bytes=_counter_delta(started.write_bytes, sample.write_bytes),
            )
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.records.append(payload)
            if self.sink is not None:
                try:
                    self.sink(payload)
                except Exception:
                    log.warning("graph stage receipt sink failed", exc_info=True)
                return
            target = self.path
            if target is None:
                from ..core.config import get_config

                target = get_config().derived_root / "graph" / "stage_receipts.ndjson"
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                log.warning("graph stage receipt write failed: path=%s", target, exc_info=True)


def _counter_delta(start: int | None, end: int | None) -> int | None:
    return max(0, end - start) if start is not None and end is not None else None


def _is_cancelled(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit)) or type(exc).__name__ == "CancelledError"


@contextmanager
def recorded_stage(
    recorder: GraphStageRecorder | None,
    stage: str,
    *,
    window_start: date | str,
    window_end: date | str,
    node_count: Callable[[], int],
    edge_count: Callable[[], int],
):
    if recorder is None:
        yield None
        return
    token, started = recorder.start(
        stage,
        window_start=window_start,
        window_end=window_end,
        node_count=node_count(),
        edge_count=edge_count(),
    )
    try:
        yield token
    except BaseException as exc:
        status = "cancelled" if _is_cancelled(exc) else "failed"
        recorder.finish(
            token,
            started,
            status=status,
            node_count=node_count(),
            edge_count=edge_count(),
            error=exc,
        )
        raise
    else:
        recorder.finish(
            token,
            started,
            status="completed",
            node_count=node_count(),
            edge_count=edge_count(),
        )


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
