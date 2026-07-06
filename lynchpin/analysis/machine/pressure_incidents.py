"""Pressure-incident interpretation over promoted machine telemetry.

sinnix-kx4: detect sustained PSI-spike windows (memory/io ``some_avg10``
above threshold, held for several consecutive samples) and annotate each
with the reclaim/kill telemetry sinnix-fjq added to the capture schema:
raw ``/proc/vmstat`` reclaim-counter deltas, top-N cgroup memory deltas,
kill events observed in the window, and active workloads (shell/xtask/git)
overlapping it. This is the "why did the host feel bad right then" product —
distinct from ``episodes.py`` (which detects a broader set of machine-state
episode kinds at avg60/avg300 PSI resolution and does not join reclaim/kill
telemetry) and from ``context.py`` (which joins *episodes*, not incidents, to
workload windows).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path

PRESSURE_DETECTOR_VERSION = "psi-spike-v1"

# PSI "some" (>=1 task stalled) at the fastest-reacting avg10 window, per the
# sinnix-kx4 ask — episodes.py's memory_pressure/io_pressure kinds use
# avg60/avg300 for a broader, less noisy sweep; incidents deliberately trade
# some noise for the ability to catch short (sub-minute) spikes.
DEFAULT_MEMORY_PSI_THRESHOLD = 10.0
DEFAULT_IO_PSI_THRESHOLD = 10.0
# At the ~10s telemetry cadence, 2 consecutive samples is a ~10-20s sustained
# floor — enough to reject single-sample blips without demanding the multi-
# minute persistence episodes.py requires for its broader kinds.
DEFAULT_MIN_SUSTAINED_SAMPLES = 2
DEFAULT_MERGE_GAP = timedelta(minutes=2)
# Padding applied to the DETECTED spike window (not the vmstat/PSI trigger
# window itself) before joining kill events, cgroup deltas, and workloads —
# a kill or a workload command can start slightly before or land slightly
# after the PSI samples that made the window trip.
DEFAULT_WINDOW_PAD = timedelta(minutes=2)
DEFAULT_TOP_N = 8

_VMSTAT_FIELDS: tuple[str, ...] = (
    "vmstat_workingset_refault_file",
    "vmstat_workingset_refault_anon",
    "vmstat_workingset_activate_file",
    "vmstat_workingset_activate_anon",
    "vmstat_pgscan_kswapd",
    "vmstat_pgscan_direct",
    "vmstat_pgsteal_kswapd",
    "vmstat_pgsteal_direct",
    "vmstat_pswpin",
    "vmstat_pswpout",
    "vmstat_allocstall_normal",
    "vmstat_allocstall_movable",
    "vmstat_oom_kill",
)


@dataclass(frozen=True)
class VmstatDelta:
    field: str
    start_value: int | None
    end_value: int | None
    delta: int | None
    per_hour: float | None


@dataclass(frozen=True)
class CgroupMemoryDelta:
    label: str
    scope: str
    control_group: str
    start_bytes: int | None
    end_bytes: int | None
    peak_bytes: int | None
    delta_bytes: int | None


@dataclass(frozen=True)
class PressureKillEvent:
    observed_at: datetime
    killer: str
    victim_comm: str | None
    victim_pid: int | None
    victim_rss_mib: int | None
    oom_score: int | None
    raw_line: str


@dataclass(frozen=True)
class ActiveWorkload:
    source: str
    window_id: str
    summary: str
    work_kind: str | None
    projects: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    overlap_seconds: float


@dataclass(frozen=True)
class PressureIncident:
    incident_id: str
    host: str
    focus: str  # "memory", "io", or "memory+io"
    started_at: datetime
    ended_at: datetime
    sample_count: int
    peak_memory_psi_some_avg10: float | None
    peak_io_psi_some_avg10: float | None
    vmstat_deltas: tuple[VmstatDelta, ...]
    top_cgroup_memory_deltas: tuple[CgroupMemoryDelta, ...]
    kill_events: tuple[PressureKillEvent, ...]
    active_workloads: tuple[ActiveWorkload, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PressureIncidentAnalysis:
    detector_version: str
    memory_psi_threshold: float
    io_psi_threshold: float
    min_sustained_samples: int
    incident_count: int
    incidents: list[PressureIncident]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_pressure_incidents(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    memory_psi_threshold: float = DEFAULT_MEMORY_PSI_THRESHOLD,
    io_psi_threshold: float = DEFAULT_IO_PSI_THRESHOLD,
    min_sustained_samples: int = DEFAULT_MIN_SUSTAINED_SAMPLES,
    merge_gap: timedelta = DEFAULT_MERGE_GAP,
    window_pad: timedelta = DEFAULT_WINDOW_PAD,
    top_n: int = DEFAULT_TOP_N,
    include_workloads: bool = True,
) -> PressureIncidentAnalysis:
    """Detect sustained memory/io PSI-spike windows and enrich each.

    Reads promoted telemetry only (``machine_metric_sample``,
    ``machine_cgroup_memory_sample``, ``machine_kill_event``, plus workload
    sources via ``context.py`` for active-workload overlap). Missing tables
    or empty windows are reported as caveats, never coerced to a silent zero.
    """
    resolved_path = path or substrate_path()
    with connect(resolved_path, read_only=True) as conn:
        rows = _metric_rows(conn, start=start, end=end)
        spikes = [
            row
            for row in rows
            if _is_spike(row, memory_psi_threshold, io_psi_threshold)
        ]
        grouped = _group_by_host(spikes)
        incident_row_groups: list[tuple[str, list[dict[str, Any]]]] = []
        for host, host_rows in grouped.items():
            for run in _merge_sustained_runs(
                host_rows, merge_gap=merge_gap, min_sustained_samples=min_sustained_samples
            ):
                incident_row_groups.append((host, run))

        # Workload windows are collected ONCE for the whole analysis window
        # (not per incident): each collector re-parses/re-queries a source
        # (terminal NDJSON, work_observation, git, deep_work), so doing it
        # per incident was O(incident_count) re-parses of the same files —
        # prohibitively slow over a multi-week window with many incidents.
        all_workloads, workload_caveats = (
            _collect_all_workloads(start=start, end=end, incident_row_groups=incident_row_groups,
                                    window_pad=window_pad, path=resolved_path)
            if include_workloads
            else ([], [])
        )

        incidents = [
            _build_incident(
                conn,
                host=host,
                run=run,
                window_pad=window_pad,
                top_n=top_n,
                memory_psi_threshold=memory_psi_threshold,
                io_psi_threshold=io_psi_threshold,
                all_workloads=all_workloads,
                workload_caveats=workload_caveats,
            )
            for host, run in incident_row_groups
        ]
    incidents.sort(key=lambda incident: (incident.started_at, incident.host))

    caveats: list[str] = []
    if not rows:
        caveats.append("machine_metric_sample has no rows in this window")
    elif not incidents:
        caveats.append(
            "no sustained PSI spike (memory or io some_avg10) crossed the "
            f"configured threshold ({memory_psi_threshold}/{io_psi_threshold}) "
            f"for >= {min_sustained_samples} consecutive samples"
        )

    return PressureIncidentAnalysis(
        detector_version=PRESSURE_DETECTOR_VERSION,
        memory_psi_threshold=memory_psi_threshold,
        io_psi_threshold=io_psi_threshold,
        min_sustained_samples=min_sustained_samples,
        incident_count=len(incidents),
        incidents=incidents,
        caveats=caveats,
    )


def write_machine_pressure_incidents(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    **kwargs: Any,
) -> PressureIncidentAnalysis:
    analysis = analyze_machine_pressure_incidents(start=start, end=end, path=path, **kwargs)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _window_clause(start: date | None, end: date | None, column: str = "observed_at") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append(f"CAST({column} AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append(f"CAST({column} AS DATE) <= ?")
        params.append(end)
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _metric_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    metric_rows = latest_machine_rows("machine_metric_sample")
    vmstat_cols = ", ".join(_VMSTAT_FIELDS)
    rows = conn.execute(
        f"""
        SELECT
            observed_at, host,
            memory_psi_some_avg10, io_psi_some_avg10,
            {vmstat_cols}
        FROM ({metric_rows})
        {where}
        ORDER BY host, observed_at
        """,
        params,
    ).fetchall()
    columns = ["observed_at", "host", "memory_psi_some_avg10", "io_psi_some_avg10", *_VMSTAT_FIELDS]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _is_spike(row: dict[str, Any], memory_threshold: float, io_threshold: float) -> bool:
    memory_psi = row.get("memory_psi_some_avg10")
    io_psi = row.get("io_psi_some_avg10")
    mem_hit = memory_psi is not None and float(memory_psi) >= memory_threshold
    io_hit = io_psi is not None and float(io_psi) >= io_threshold
    return mem_hit or io_hit


def _group_by_host(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["host"]), []).append(row)
    return grouped


def _merge_sustained_runs(
    rows: list[dict[str, Any]],
    *,
    merge_gap: timedelta,
    min_sustained_samples: int,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: row["observed_at"])
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in ordered:
        if current and row["observed_at"] - current[-1]["observed_at"] > merge_gap:
            runs.append(current)
            current = []
        current.append(row)
    if current:
        runs.append(current)
    return [run for run in runs if len(run) >= min_sustained_samples]


def _focus(run: list[dict[str, Any]], *, memory_threshold: float, io_threshold: float) -> str:
    mem_hit = any(
        row.get("memory_psi_some_avg10") is not None
        and float(row["memory_psi_some_avg10"]) >= memory_threshold
        for row in run
    )
    io_hit = any(
        row.get("io_psi_some_avg10") is not None and float(row["io_psi_some_avg10"]) >= io_threshold
        for row in run
    )
    if mem_hit and io_hit:
        return "memory+io"
    return "memory" if mem_hit else "io"


def _build_incident(
    conn: Any,
    *,
    host: str,
    run: list[dict[str, Any]],
    window_pad: timedelta,
    top_n: int,
    memory_psi_threshold: float,
    io_psi_threshold: float,
    all_workloads: list["ActiveWorkload"],
    workload_caveats: list[str],
) -> PressureIncident:
    started_at = run[0]["observed_at"]
    ended_at = run[-1]["observed_at"]
    padded_start = started_at - window_pad
    padded_end = ended_at + window_pad

    memory_values = [
        float(row["memory_psi_some_avg10"])
        for row in run
        if row.get("memory_psi_some_avg10") is not None
    ]
    io_values = [
        float(row["io_psi_some_avg10"]) for row in run if row.get("io_psi_some_avg10") is not None
    ]

    vmstat_deltas = _vmstat_deltas(run, started_at=started_at, ended_at=ended_at)
    cgroup_deltas, cgroup_caveats = _cgroup_memory_deltas(
        conn, host=host, start=padded_start, end=padded_end, top_n=top_n
    )
    kill_events, kill_caveats = _kill_events_in_window(
        conn, host=host, start=padded_start, end=padded_end
    )
    workloads = _overlapping_workloads(all_workloads, start=padded_start, end=padded_end)

    incident_id = (
        f"{host}:{started_at.astimezone(timezone.utc).isoformat()}:"
        f"{ended_at.astimezone(timezone.utc).isoformat()}"
    )

    return PressureIncident(
        incident_id=incident_id,
        host=host,
        focus=_focus(run, memory_threshold=memory_psi_threshold, io_threshold=io_psi_threshold),
        started_at=started_at,
        ended_at=ended_at,
        sample_count=len(run),
        peak_memory_psi_some_avg10=max(memory_values) if memory_values else None,
        peak_io_psi_some_avg10=max(io_values) if io_values else None,
        vmstat_deltas=vmstat_deltas,
        top_cgroup_memory_deltas=cgroup_deltas,
        kill_events=kill_events,
        active_workloads=workloads,
        caveats=(*cgroup_caveats, *kill_caveats, *workload_caveats),
    )


def _vmstat_deltas(
    run: list[dict[str, Any]], *, started_at: datetime, ended_at: datetime
) -> tuple[VmstatDelta, ...]:
    duration_hours = max((ended_at - started_at).total_seconds() / 3600.0, 1e-9)
    deltas: list[VmstatDelta] = []
    for field in _VMSTAT_FIELDS:
        values: list[tuple[datetime, int]] = []
        for row in run:
            raw_value = row.get(field)
            if raw_value is not None:
                values.append((row["observed_at"], int(raw_value)))
        if not values:
            deltas.append(VmstatDelta(field=field, start_value=None, end_value=None, delta=None, per_hour=None))
            continue
        values.sort(key=lambda item: item[0])
        start_value = values[0][1]
        end_value = values[-1][1]
        delta = end_value - start_value
        deltas.append(
            VmstatDelta(
                field=field,
                start_value=start_value,
                end_value=end_value,
                delta=delta,
                per_hour=round(delta / duration_hours, 2),
            )
        )
    return tuple(deltas)


def _cgroup_memory_deltas(
    conn: Any, *, host: str, start: datetime, end: datetime, top_n: int
) -> tuple[tuple[CgroupMemoryDelta, ...], tuple[str, ...]]:
    cgroup_rows_sql = latest_machine_rows("machine_cgroup_memory_sample")
    try:
        rows = conn.execute(
            f"""
            SELECT
                label, scope, control_group,
                arg_min(memory_current_bytes, observed_at) AS start_bytes,
                arg_max(memory_current_bytes, observed_at) AS end_bytes,
                max(memory_current_bytes) AS peak_bytes
            FROM ({cgroup_rows_sql})
            WHERE host = ? AND observed_at >= ? AND observed_at <= ?
            GROUP BY label, scope, control_group
            """,
            [host, start, end],
        ).fetchall()
    except Exception as exc:  # pragma: no cover — table absent on older substrates
        return (), (f"machine_cgroup_memory_sample query failed: {exc}",)

    deltas: list[CgroupMemoryDelta] = []
    for label, scope, control_group, start_bytes, end_bytes, peak_bytes in rows:
        delta_bytes = (
            int(end_bytes) - int(start_bytes) if start_bytes is not None and end_bytes is not None else None
        )
        deltas.append(
            CgroupMemoryDelta(
                label=str(label),
                scope=str(scope),
                control_group=str(control_group),
                start_bytes=start_bytes,
                end_bytes=end_bytes,
                peak_bytes=peak_bytes,
                delta_bytes=delta_bytes,
            )
        )
    deltas.sort(key=lambda item: abs(item.delta_bytes) if item.delta_bytes is not None else -1, reverse=True)
    caveats = () if deltas else ("no cgroup memory samples for this host in the incident window",)
    return tuple(deltas[:top_n]), caveats


def _kill_events_in_window(
    conn: Any, *, host: str, start: datetime, end: datetime
) -> tuple[tuple[PressureKillEvent, ...], tuple[str, ...]]:
    kill_rows_sql = latest_machine_rows("machine_kill_event")
    try:
        rows = conn.execute(
            f"""
            SELECT observed_at, killer, victim_comm, victim_pid, victim_rss_mib,
                   oom_score, raw_line
            FROM ({kill_rows_sql})
            WHERE host = ? AND observed_at >= ? AND observed_at <= ?
            ORDER BY observed_at
            """,
            [host, start, end],
        ).fetchall()
    except Exception as exc:  # pragma: no cover — table absent on older substrates
        return (), (f"machine_kill_event query failed: {exc}",)

    events = tuple(
        PressureKillEvent(
            observed_at=row[0],
            killer=str(row[1]),
            victim_comm=row[2],
            victim_pid=row[3],
            victim_rss_mib=row[4],
            oom_score=row[5],
            raw_line=str(row[6]),
        )
        for row in rows
    )
    return events, ()


def _collect_all_workloads(
    *,
    start: date | None,
    end: date | None,
    incident_row_groups: list[tuple[str, list[dict[str, Any]]]],
    window_pad: timedelta,
    path: Path,
) -> tuple[list[Any], list[str]]:
    """Collect workload windows ONCE for the whole analysis run.

    Each collector (terminal NDJSON, work_observation, git, deep_work)
    re-parses or re-queries its source; doing this per incident would be
    O(incident_count) redundant re-parses of the same files over the same
    window — prohibitively slow once a multi-week run has more than a
    handful of incidents. Bounds default to the incident rows' own
    observed_at span (padded a day either side) when start/end are None.
    """
    from lynchpin.analysis.machine.context import _collect_workload_windows

    if start is not None and end is not None:
        window_start_date = start
        window_end_date = end
    else:
        all_timestamps = [
            row["observed_at"] for _host, run in incident_row_groups for row in run
        ]
        if not all_timestamps:
            return [], []
        window_start_date = (min(all_timestamps) - window_pad - timedelta(days=1)).date()
        window_end_date = (max(all_timestamps) + window_pad + timedelta(days=1)).date()

    windows, caveats = _collect_workload_windows(
        start=window_start_date,
        end=window_end_date,
        path=path,
        include_polylogue=False,
        include_ambient_sources=True,
    )
    return windows, caveats


def _overlapping_workloads(
    all_workloads: list[Any], *, start: datetime, end: datetime
) -> tuple[ActiveWorkload, ...]:
    """Filter pre-collected workload windows to those overlapping [start, end]."""
    active: list[ActiveWorkload] = []
    for window in all_workloads:
        overlap_start = max(window.started_at, start)
        overlap_end = min(window.ended_at, end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()
        if overlap_seconds <= 0:
            continue
        active.append(
            ActiveWorkload(
                source=window.source,
                window_id=window.window_id,
                summary=window.summary,
                work_kind=window.work_kind,
                projects=window.projects,
                started_at=window.started_at,
                ended_at=window.ended_at,
                overlap_seconds=round(overlap_seconds, 1),
            )
        )
    active.sort(key=lambda item: item.overlap_seconds, reverse=True)
    return tuple(active)


__all__ = [
    "ActiveWorkload",
    "CgroupMemoryDelta",
    "PressureIncident",
    "PressureIncidentAnalysis",
    "PressureKillEvent",
    "VmstatDelta",
    "PRESSURE_DETECTOR_VERSION",
    "analyze_machine_pressure_incidents",
    "write_machine_pressure_incidents",
]
