"""Typed machine-state episode detection over substrate telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Literal

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path

Direction = Literal["high", "low", "state"]


@dataclass(frozen=True)
class MachineEpisodeKindDefinition:
    kind: str
    label: str
    definition: str
    trigger_contract: str
    interpretation_boundary: str


@dataclass(frozen=True)
class MachineEpisodeEvidence:
    source_table: str
    metric: str
    direction: Direction
    value: float | str | None
    threshold: float | str | None
    reason: str


@dataclass(frozen=True)
class MachineEpisode:
    kind: str
    host: str
    started_at: datetime
    ended_at: datetime
    sample_count: int
    severity: float
    confidence: float
    evidence: tuple[MachineEpisodeEvidence, ...]
    sources: tuple[str, ...]
    caveats: tuple[str, ...]
    subject: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class MachineEpisodeCoverage:
    metric_samples: int
    gpu_samples: int
    network_samples: int
    service_samples: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    hosts: tuple[str, ...]


@dataclass(frozen=True)
class MachineEpisodeAnalysis:
    coverage: MachineEpisodeCoverage
    kind_definitions: tuple[MachineEpisodeKindDefinition, ...]
    episode_count: int
    episodes: list[MachineEpisode]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Rule:
    kind: str
    metric: str
    direction: Direction
    absolute_threshold: float | None
    robust_multiplier: float = 6.0
    min_baseline_count: int = 12


@dataclass(frozen=True)
class _Point:
    kind: str
    host: str
    observed_at: datetime
    severity: float
    confidence: float
    evidence: MachineEpisodeEvidence
    source: str
    subject: str | None = None
    caveats: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None


_METRIC_RULES: tuple[_Rule, ...] = (
    _Rule("load_pressure", "load_1m", "high", 20.0),
    _Rule("cpu_saturation", "cpu_psi_some", "high", 10.0),
    _Rule("memory_pressure", "mem_avail_mb", "low", 4096.0),
    _Rule("memory_pressure", "swap_used_mb", "high", 1024.0),
    _Rule("memory_pressure", "memory_psi_some", "high", 10.0),
    _Rule("memory_pressure", "memory_psi_full", "high", 1.0),
    _Rule("io_pressure", "io_psi_some", "high", 10.0),
    _Rule("io_pressure", "io_psi_full", "high", 1.0),
    _Rule("scheduler_latency", "latency_oversleep_ms", "high", 50.0),
    _Rule("blocked_task_pressure", "dstate_task_count", "high", 1.0),
    _Rule("gpu_power_or_thermal", "gpu_temp_c", "high", 83.0),
)

_NETWORK_RULES: tuple[_Rule, ...] = (
    _Rule("network_degraded", "dns_ms", "high", 200.0),
    _Rule("network_degraded", "ping_loss_pct", "high", 1.0),
    _Rule("network_degraded", "ping_rtt_ms", "high", 100.0),
)

EPISODE_KIND_DEFINITIONS: tuple[MachineEpisodeKindDefinition, ...] = (
    MachineEpisodeKindDefinition(
        kind="load_pressure",
        label="Load Pressure",
        definition="The host load average is high enough to indicate a backlog of runnable or uninterruptible tasks.",
        trigger_contract="Emitted from load_1m when it crosses the configured absolute or robust high threshold.",
        interpretation_boundary="This is not CPU saturation by itself: Linux load includes D-state and other uninterruptible waits.",
    ),
    MachineEpisodeKindDefinition(
        kind="cpu_saturation",
        label="CPU Saturation",
        definition="CPU pressure stall information indicates runnable work is waiting for CPU time.",
        trigger_contract="Emitted from CPU PSI some-average fields when they cross the configured high threshold.",
        interpretation_boundary="Do not infer CPU saturation from load_1m alone; use load_pressure unless CPU PSI is present.",
    ),
    MachineEpisodeKindDefinition(
        kind="memory_pressure",
        label="Memory Pressure",
        definition="Available memory or memory PSI indicates reclaim/availability pressure.",
        trigger_contract="Emitted from low mem_avail_mb, material swap use, or high memory PSI some/full averages.",
        interpretation_boundary="This does not identify the responsible process without a joined below/process window.",
    ),
    MachineEpisodeKindDefinition(
        kind="io_pressure",
        label="I/O Pressure",
        definition="I/O PSI indicates tasks are stalled on storage or filesystem I/O.",
        trigger_contract="Emitted from I/O PSI some/full averages when they cross configured high thresholds.",
        interpretation_boundary="This does not identify the device, file path, or process without joined below or workload evidence.",
    ),
    MachineEpisodeKindDefinition(
        kind="scheduler_latency",
        label="Scheduler Latency",
        definition="The host reports timer or scheduling oversleep beyond the expected sampling cadence.",
        trigger_contract="Emitted from latency_oversleep_ms when it crosses the configured high threshold.",
        interpretation_boundary="This is a host latency symptom, not a root cause; join load, PSI, and process evidence before attributing it.",
    ),
    MachineEpisodeKindDefinition(
        kind="blocked_task_pressure",
        label="Blocked Task Pressure",
        definition="The host reports tasks in uninterruptible sleep/D-state.",
        trigger_contract="Emitted from dstate_task_count when it crosses the configured high threshold.",
        interpretation_boundary="This is not scheduler latency by itself; it usually requires I/O, device, or process attribution.",
    ),
    MachineEpisodeKindDefinition(
        kind="gpu_power_or_thermal",
        label="GPU Thermal Pressure",
        definition="GPU temperature crosses the thermal threshold used as a risk signal.",
        trigger_contract="Emitted from gpu_temp_c when it crosses the configured high threshold.",
        interpretation_boundary="High GPU utilization alone is workload context, not a power/thermal problem.",
    ),
    MachineEpisodeKindDefinition(
        kind="gpu_link_regime",
        label="GPU PCIe Link Regime",
        definition="The observed GPU PCIe generation/width is below the best valid link state seen in the analysis window.",
        trigger_contract="Emitted from positive gpu_pcie_gen/gpu_pcie_width states below the best observed positive gen/width.",
        interpretation_boundary="This is a hardware-state regime, not proof that a workload was bottlenecked by PCIe bandwidth.",
    ),
    MachineEpisodeKindDefinition(
        kind="network_degraded",
        label="Network Degraded",
        definition="Network probe latency, DNS latency, or packet loss crosses configured thresholds.",
        trigger_contract="Emitted from dns_ms or parsed ping latency/loss fields.",
        interpretation_boundary="This covers probe health only; it does not prove an application-level network failure.",
    ),
    MachineEpisodeKindDefinition(
        kind="service_instability",
        label="Service Instability",
        definition="A sampled systemd/user unit is in a failed state.",
        trigger_contract="Emitted only when active_state or sub_state is failed.",
        interpretation_boundary="Inactive/dead one-shot or timer units are not instability.",
    ),
)


def analyze_machine_episodes(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    max_gap: timedelta = timedelta(minutes=3),
) -> MachineEpisodeAnalysis:
    """Detect machine-state episodes from promoted telemetry rows.

    Episodes are deliberately dimension-preserving: metric, GPU, network, and
    service rows can all emit evidence, but the result keeps source tables and
    triggering measurements attached to every episode.
    """
    with connect(path or substrate_path(), read_only=True) as conn:
        coverage = _coverage(conn, start=start, end=end)
        metric_thresholds = _metric_thresholds(conn, start=start, end=end)
        metric_rows = _metric_candidate_rows(conn, start=start, end=end, thresholds=metric_thresholds)
        metric_link_rows = _metric_gpu_link_rows(conn, start=start, end=end)
        gpu_rows = _gpu_rows(conn, start=start, end=end)
        network_rows = _network_rows(conn, start=start, end=end)
        service_rows = _service_rows(conn, start=start, end=end)

    points: list[_Point] = []
    points.extend(_metric_points(metric_rows, thresholds=metric_thresholds))
    points.extend(_gpu_link_points(metric_link_rows, gpu_rows))
    points.extend(_network_points(network_rows))
    points.extend(_service_points(service_rows))

    episodes = _merge_points(points, max_gap=max_gap)
    caveats = _analysis_caveats(coverage, episodes)
    return MachineEpisodeAnalysis(
        coverage=coverage,
        kind_definitions=EPISODE_KIND_DEFINITIONS,
        episode_count=len(episodes),
        episodes=episodes,
        caveats=caveats,
    )


def write_machine_episode_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> MachineEpisodeAnalysis:
    analysis = analyze_machine_episodes(start=start, end=end, path=path)
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


def _coverage(conn: Any, *, start: date | None, end: date | None) -> MachineEpisodeCoverage:
    counts: dict[str, int] = {}
    firsts: list[datetime] = []
    lasts: list[datetime] = []
    hosts: set[str] = set()
    for table in ("machine_metric_sample", "machine_gpu_sample", "machine_network_sample", "machine_service_state"):
        where, params = _window_clause(start, end)
        rows_sql = latest_machine_rows(table)
        row = conn.execute(
            f"SELECT count(*), min(observed_at), max(observed_at) FROM ({rows_sql}) {where}",
            params,
        ).fetchone()
        counts[table] = int(row[0])
        if row[1] is not None:
            firsts.append(row[1])
        if row[2] is not None:
            lasts.append(row[2])
        host_rows = conn.execute(f"SELECT DISTINCT host FROM ({rows_sql}) {where}", params).fetchall()
        hosts.update(str(host) for (host,) in host_rows)
    return MachineEpisodeCoverage(
        metric_samples=counts["machine_metric_sample"],
        gpu_samples=counts["machine_gpu_sample"],
        network_samples=counts["machine_network_sample"],
        service_samples=counts["machine_service_state"],
        first_observed_at=min(firsts) if firsts else None,
        last_observed_at=max(lasts) if lasts else None,
        hosts=tuple(sorted(hosts)),
    )


def _metric_thresholds(conn: Any, *, start: date | None, end: date | None) -> dict[str, float | None]:
    return {
        rule.metric: _sql_threshold(conn, rule, start=start, end=end)
        for rule in _METRIC_RULES
    }


def _sql_threshold(conn: Any, rule: _Rule, *, start: date | None, end: date | None) -> float | None:
    where, params = _window_clause(start, end)
    expr = _metric_expr(rule.metric)
    metric_rows = latest_machine_rows("machine_metric_sample")
    row = conn.execute(
        f"""
        WITH vals AS (
            SELECT {expr}::DOUBLE AS value
            FROM ({metric_rows})
            {where}
        ),
        clean AS (
            SELECT value FROM vals WHERE value IS NOT NULL
        ),
        med AS (
            SELECT median(value) AS median_value, count(*) AS n FROM clean
        )
        SELECT med.median_value, med.n, median(abs(clean.value - med.median_value)) AS mad
        FROM clean, med
        GROUP BY med.median_value, med.n
        """,
        params,
    ).fetchone()
    if row is None or row[1] is None or int(row[1]) == 0:
        return rule.absolute_threshold
    if int(row[1]) < rule.min_baseline_count or row[2] is None:
        return rule.absolute_threshold
    median_value = float(row[0])
    mad = float(row[2])
    scale = 1.4826 * mad
    if scale <= 1e-9:
        return rule.absolute_threshold
    robust = median_value + rule.robust_multiplier * scale if rule.direction == "high" else median_value - rule.robust_multiplier * scale
    if rule.absolute_threshold is None:
        return robust
    return max(rule.absolute_threshold, robust) if rule.direction == "high" else min(rule.absolute_threshold, robust)


def _metric_expr(metric: str) -> str:
    expressions = {
        "load_1m": "load_1m",
        "cpu_psi_some": "coalesce(cpu_psi_some_avg60, cpu_psi_some_avg300)",
        "mem_avail_mb": "mem_avail_mb",
        "swap_used_mb": "swap_used_mb",
        "memory_psi_some": "coalesce(memory_psi_some_avg60, memory_psi_some_avg300)",
        "memory_psi_full": "coalesce(memory_psi_full_avg60, memory_psi_full_avg300)",
        "io_psi_some": "coalesce(io_psi_some_avg10, io_psi_some_avg60, io_psi_some_avg300)",
        "io_psi_full": "coalesce(io_psi_full_avg10, io_psi_full_avg60, io_psi_full_avg300)",
        "latency_oversleep_ms": "latency_oversleep_ms",
        "dstate_task_count": "dstate_task_count",
        "gpu_temp_c": "gpu_temp_c",
        "gpu_util_pct": "gpu_util_pct",
    }
    return expressions[metric]


def _metric_candidate_rows(
    conn: Any,
    *,
    start: date | None,
    end: date | None,
    thresholds: dict[str, float | None],
) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    metric_rows = latest_machine_rows("machine_metric_sample")
    filters: list[str] = []
    filter_params: list[Any] = []
    for rule in _METRIC_RULES:
        threshold = thresholds.get(rule.metric)
        if threshold is None:
            continue
        op = ">=" if rule.direction == "high" else "<="
        filters.append(f"{_metric_expr(rule.metric)} {op} ?")
        filter_params.append(threshold)
    if filters:
        clause = "(" + " OR ".join(filters) + ")"
        if where:
            where = f"{where} AND {clause}"
        else:
            where = f"WHERE {clause}"
        params.extend(filter_params)
    rows = conn.execute(
        f"""
        SELECT
            observed_at,
            host,
            coalesce(load_1m, 0.0) AS load_1m,
            coalesce(cpu_psi_some_avg60, cpu_psi_some_avg300, 0.0) AS cpu_psi_some,
            mem_avail_mb,
            swap_used_mb,
            coalesce(memory_psi_some_avg60, memory_psi_some_avg300, 0.0) AS memory_psi_some,
            coalesce(memory_psi_full_avg60, memory_psi_full_avg300, 0.0) AS memory_psi_full,
            coalesce(io_psi_some_avg10, io_psi_some_avg60, io_psi_some_avg300, 0.0) AS io_psi_some,
            coalesce(io_psi_full_avg10, io_psi_full_avg60, io_psi_full_avg300, 0.0) AS io_psi_full,
            latency_oversleep_ms,
            dstate_task_count,
            gpu_temp_c,
            gpu_util_pct,
            gpu_power_w,
            gpu_pcie_gen,
            gpu_pcie_width,
            gap_codes
        FROM ({metric_rows})
        {where}
        ORDER BY observed_at, host
        """,
        params,
    ).fetchall()
    columns = [
        "observed_at", "host", "load_1m", "cpu_psi_some", "mem_avail_mb", "swap_used_mb",
        "memory_psi_some", "memory_psi_full", "io_psi_some", "io_psi_full",
        "latency_oversleep_ms", "dstate_task_count", "gpu_temp_c", "gpu_util_pct",
        "gpu_power_w", "gpu_pcie_gen", "gpu_pcie_width", "gap_codes",
    ]
    return [dict(zip(columns, row)) for row in rows]


def _metric_gpu_link_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    metric_rows = latest_machine_rows("machine_metric_sample")
    best = conn.execute(
        f"""
        SELECT max(gpu_pcie_gen), max(gpu_pcie_width)
        FROM ({metric_rows})
        {where}
        """,
        params,
    ).fetchone()
    if best is None or best[0] is None or best[1] is None:
        return []
    best_gen = int(best[0])
    best_width = int(best[1])
    link_filter = (
        "gpu_pcie_gen IS NOT NULL AND gpu_pcie_width IS NOT NULL "
        "AND gpu_pcie_gen > 0 AND gpu_pcie_width > 0 "
        "AND (gpu_pcie_gen < ? OR gpu_pcie_width < ?)"
    )
    if where:
        where = f"{where} AND {link_filter}"
    else:
        where = f"WHERE {link_filter}"
    params = [*params, best_gen, best_width]
    rows = conn.execute(
        f"""
        SELECT observed_at, host, gpu_pcie_gen, gpu_pcie_width, gpu_util_pct, gpu_power_w
        FROM ({metric_rows})
        {where}
        ORDER BY observed_at, host
        """,
        params,
    ).fetchall()
    columns = ["observed_at", "host", "gpu_pcie_gen", "gpu_pcie_width", "gpu_util_pct", "gpu_power_w"]
    result = [dict(zip(columns, row)) for row in rows]
    for row in result:
        row["_best_gpu_pcie_gen"] = best_gen
        row["_best_gpu_pcie_width"] = best_width
    return result


def _gpu_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    gpu_rows = latest_machine_rows("machine_gpu_sample")
    rows = conn.execute(
        f"""
        SELECT observed_at, host, gpu_pcie_gen, gpu_pcie_width, gpu_util_pct, gpu_power_w,
               gpu_temp_c, gpu_power_limit_w
        FROM ({gpu_rows})
        {where}
        ORDER BY observed_at, host
        """,
        params,
    ).fetchall()
    columns = [
        "observed_at", "host", "gpu_pcie_gen", "gpu_pcie_width", "gpu_util_pct",
        "gpu_power_w", "gpu_temp_c", "gpu_power_limit_w",
    ]
    return [dict(zip(columns, row)) for row in rows]


def _network_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    network_rows = latest_machine_rows("machine_network_sample")
    rows = conn.execute(
        f"""
        SELECT observed_at, host, interface, dns_ms, ping, gap_codes
        FROM ({network_rows})
        {where}
        ORDER BY observed_at, host, interface
        """,
        params,
    ).fetchall()
    columns = ["observed_at", "host", "interface", "dns_ms", "ping", "gap_codes"]
    return [dict(zip(columns, row)) for row in rows]


def _service_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    failed_clause = "(active_state = 'failed' OR sub_state = 'failed')"
    where = f"{where} AND {failed_clause}" if where else f"WHERE {failed_clause}"
    service_rows = latest_machine_rows("machine_service_state")
    rows = conn.execute(
        f"""
        SELECT observed_at, host, unit, scope, active_state, sub_state, memory_current_bytes
        FROM ({service_rows})
        {where}
        ORDER BY observed_at, host, scope, unit
        """,
        params,
    ).fetchall()
    columns = ["observed_at", "host", "unit", "scope", "active_state", "sub_state", "memory_current_bytes"]
    return [dict(zip(columns, row)) for row in rows]


def _metric_points(rows: list[dict[str, Any]], *, thresholds: dict[str, float | None]) -> list[_Point]:
    points: list[_Point] = []
    for row in rows:
        for rule in _METRIC_RULES:
            value = _float(row.get(rule.metric))
            threshold = thresholds[rule.metric]
            if value is None or threshold is None or not _trips(value, threshold, rule.direction):
                continue
            severity = _metric_severity(rule.metric, value, threshold, rule.direction)
            confidence = _confidence(row_count=len(rows), has_absolute=rule.absolute_threshold is not None)
            reason = f"{rule.metric} {rule.direction} threshold crossed"
            points.append(_Point(
                kind=rule.kind,
                host=str(row["host"]),
                observed_at=row["observed_at"],
                severity=severity,
                confidence=confidence,
                evidence=MachineEpisodeEvidence(
                    source_table="machine_metric_sample",
                    metric=rule.metric,
                    direction=rule.direction,
                    value=round(value, 4),
                    threshold=round(threshold, 4),
                    reason=reason,
                ),
                source="machine_metric_sample",
                caveats=_gap_caveats(row.get("gap_codes")),
            ))
    return points


def _gpu_link_points(metric_rows: list[dict[str, Any]], gpu_rows: list[dict[str, Any]]) -> list[_Point]:
    rows = [
        row for row in [*metric_rows, *gpu_rows]
        if (
            row.get("gpu_pcie_gen") is not None
            and row.get("gpu_pcie_width") is not None
            and int(row["gpu_pcie_gen"]) > 0
            and int(row["gpu_pcie_width"]) > 0
        )
    ]
    if not rows:
        return []
    explicit_best = [
        (int(row["_best_gpu_pcie_gen"]), int(row["_best_gpu_pcie_width"]))
        for row in rows
        if row.get("_best_gpu_pcie_gen") is not None and row.get("_best_gpu_pcie_width") is not None
    ]
    best_gen, best_width = max(explicit_best or [(int(row["gpu_pcie_gen"]), int(row["gpu_pcie_width"])) for row in rows])
    points: list[_Point] = []
    for row in rows:
        gen = int(row["gpu_pcie_gen"])
        width = int(row["gpu_pcie_width"])
        if (gen, width) >= (best_gen, best_width):
            continue
        util = _float(row.get("gpu_util_pct")) or 0.0
        power = _float(row.get("gpu_power_w")) or 0.0
        link_ratio = (gen * width) / max(float(best_gen * best_width), 1.0)
        activity = max(min(util / 100.0, 1.0), min(power / 300.0, 1.0))
        severity = _clamp((1.0 - link_ratio) * (0.5 + 0.5 * activity))
        points.append(_Point(
            kind="gpu_link_regime",
            host=str(row["host"]),
            observed_at=row["observed_at"],
            severity=severity,
            confidence=0.9 if len(rows) >= 5 else 0.65,
            evidence=MachineEpisodeEvidence(
                source_table="machine_gpu_sample" if "gpu_power_limit_w" in row else "machine_metric_sample",
                metric="gpu_pcie_link",
                direction="state",
                value=f"gen{gen}x{width}",
                threshold=f"best_observed=gen{best_gen}x{best_width}",
                reason="GPU PCIe link is below best observed link state",
            ),
            source="machine_gpu_sample" if "gpu_power_limit_w" in row else "machine_metric_sample",
            subject=f"gen{gen}x{width}",
            payload={"gpu_pcie_gen": gen, "gpu_pcie_width": width, "best_gen": best_gen, "best_width": best_width},
        ))
    return points


def _network_points(rows: list[dict[str, Any]]) -> list[_Point]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        ping = _json_dict(row.get("ping"))
        enriched.append({
            **row,
            "ping_loss_pct": _first_number(ping, ("loss_pct", "packet_loss_pct", "packet_loss")),
            "ping_rtt_ms": _first_number(ping, ("avg_ms", "rtt_avg_ms", "latency_ms", "time_ms")),
        })
    thresholds = {rule.metric: _threshold(enriched, rule) for rule in _NETWORK_RULES}
    points: list[_Point] = []
    for row in enriched:
        for rule in _NETWORK_RULES:
            value = _float(row.get(rule.metric))
            threshold = thresholds[rule.metric]
            if value is None or threshold is None or not _trips(value, threshold, rule.direction):
                continue
            points.append(_Point(
                kind=rule.kind,
                host=str(row["host"]),
                observed_at=row["observed_at"],
                severity=_metric_severity(rule.metric, value, threshold, rule.direction),
                confidence=_confidence(row_count=len(rows), has_absolute=True),
                evidence=MachineEpisodeEvidence(
                    source_table="machine_network_sample",
                    metric=rule.metric,
                    direction=rule.direction,
                    value=round(value, 4),
                    threshold=round(threshold, 4),
                    reason=f"{rule.metric} network threshold crossed",
                ),
                source="machine_network_sample",
                subject=str(row.get("interface") or ""),
                caveats=_gap_caveats(row.get("gap_codes")),
            ))
    return points


def _service_points(rows: list[dict[str, Any]]) -> list[_Point]:
    points: list[_Point] = []
    for row in rows:
        active = row.get("active_state")
        sub = row.get("sub_state")
        failed = active == "failed" or sub == "failed"
        if not failed:
            continue
        points.append(_Point(
            kind="service_instability",
            host=str(row["host"]),
            observed_at=row["observed_at"],
            severity=1.0,
            confidence=0.95,
            evidence=MachineEpisodeEvidence(
                source_table="machine_service_state",
                metric="active_state",
                direction="state",
                value=str(active),
                threshold="not failed",
                reason="sampled service state is failed",
            ),
            source="machine_service_state",
            subject=f"{row.get('scope')}:{row.get('unit')}",
            payload={"unit": row.get("unit"), "scope": row.get("scope"), "sub_state": sub},
        ))
    return points


def _merge_points(points: list[_Point], *, max_gap: timedelta) -> list[MachineEpisode]:
    grouped: dict[tuple[str, str, str | None], list[_Point]] = {}
    for point in points:
        grouped.setdefault((point.host, point.kind, point.subject), []).append(point)

    episodes: list[MachineEpisode] = []
    for (host, kind, subject), group in grouped.items():
        group.sort(key=lambda point: point.observed_at)
        group_gap = _merge_gap(kind, max_gap)
        current: list[_Point] = []
        for point in group:
            if current and point.observed_at - current[-1].observed_at > group_gap:
                episodes.append(_episode_from_points(host, kind, subject, current))
                current = []
            current.append(point)
        if current:
            episodes.append(_episode_from_points(host, kind, subject, current))
    episodes.sort(key=lambda episode: (episode.started_at, episode.host, episode.kind, episode.subject or ""))
    return episodes


def _merge_gap(kind: str, default: timedelta) -> timedelta:
    if kind == "gpu_link_regime":
        return max(default, timedelta(minutes=30))
    return default


def _episode_from_points(host: str, kind: str, subject: str | None, points: list[_Point]) -> MachineEpisode:
    started = min(point.observed_at for point in points)
    ended = max(point.observed_at for point in points)
    evidence = tuple(_dedupe_evidence(point.evidence for point in points))
    sources = tuple(sorted({point.source for point in points}))
    caveats = tuple(sorted({caveat for point in points for caveat in point.caveats}))
    severity = round(max(point.severity for point in points), 4)
    confidence = _episode_confidence(kind, points)
    payload: dict[str, Any] = {}
    for point in points:
        if point.payload:
            payload.update(point.payload)
    return MachineEpisode(
        kind=kind,
        host=host,
        started_at=started,
        ended_at=ended,
        sample_count=len(points),
        severity=severity,
        confidence=confidence,
        evidence=evidence,
        sources=sources,
        caveats=caveats,
        subject=subject,
        payload=payload or None,
    )


def _episode_confidence(kind: str, points: list[_Point]) -> float:
    confidence = min(0.99, statistics.mean(point.confidence for point in points))
    if kind in {"service_instability", "gpu_link_regime"}:
        return round(confidence, 4)
    sample_count = len(points)
    if sample_count == 1:
        confidence = min(confidence, 0.65)
    elif sample_count < 3:
        confidence = min(confidence, 0.75)
    elif sample_count < 5:
        confidence = min(confidence, 0.85)
    return round(confidence, 4)


def _dedupe_evidence(items: Any) -> list[MachineEpisodeEvidence]:
    result: dict[tuple[str, str, str, str], MachineEpisodeEvidence] = {}
    for item in items:
        key = (item.source_table, item.metric, item.direction, str(item.threshold))
        previous = result.get(key)
        if previous is None or _more_extreme(item, previous):
            result[key] = item
    return list(result.values())


def _more_extreme(candidate: MachineEpisodeEvidence, previous: MachineEpisodeEvidence) -> bool:
    candidate_value = _float(candidate.value)
    previous_value = _float(previous.value)
    if candidate_value is None or previous_value is None:
        return False
    if candidate.direction == "low":
        return candidate_value < previous_value
    if candidate.direction == "high":
        return candidate_value > previous_value
    return False


def _threshold(rows: list[dict[str, Any]], rule: _Rule) -> float | None:
    valid = sorted(value for row in rows if (value := _float(row.get(rule.metric))) is not None)
    if not valid:
        return rule.absolute_threshold
    robust: float | None = None
    if len(valid) >= rule.min_baseline_count:
        med = statistics.median(valid)
        deviations = [abs(value - med) for value in valid]
        mad = statistics.median(deviations)
        scale = 1.4826 * mad
        if scale > 1e-9:
            robust = med + rule.robust_multiplier * scale if rule.direction == "high" else med - rule.robust_multiplier * scale
    if robust is None:
        return rule.absolute_threshold
    if rule.absolute_threshold is None:
        return robust
    return max(rule.absolute_threshold, robust) if rule.direction == "high" else min(rule.absolute_threshold, robust)


def _trips(value: float, threshold: float, direction: Direction) -> bool:
    if direction == "high":
        return value >= threshold
    if direction == "low":
        return value <= threshold
    return False


def _severity(value: float, threshold: float, direction: Direction) -> float:
    if abs(threshold) < 1e-9:
        return 1.0
    if direction == "low":
        return _clamp((threshold - value) / abs(threshold))
    return _clamp((value - threshold) / abs(threshold))


def _metric_severity(metric: str, value: float, threshold: float, direction: Direction) -> float:
    if direction == "low":
        return _severity(value, threshold, direction)
    if metric in {"io_psi_some", "io_psi_full", "cpu_psi_some", "memory_psi_some", "memory_psi_full", "gpu_temp_c"}:
        return _clamp((value - threshold) / max(100.0 - threshold, 1.0))
    if metric == "latency_oversleep_ms":
        return _clamp((value - threshold) / 450.0)
    if metric == "dstate_task_count":
        return _clamp((value - threshold) / 31.0)
    if metric == "load_1m":
        return _clamp((value - threshold) / 44.0)
    if metric in {"dns_ms", "ping_rtt_ms"}:
        return _clamp((value - threshold) / max(threshold * 4.0, 1.0))
    if metric == "ping_loss_pct":
        return _clamp((value - threshold) / max(100.0 - threshold, 1.0))
    return _severity(value, threshold, direction)


def _confidence(*, row_count: int, has_absolute: bool) -> float:
    base = 0.7 if has_absolute else 0.55
    return round(min(0.97, base + min(row_count, 100) / 400.0), 4)


def _analysis_caveats(coverage: MachineEpisodeCoverage, episodes: list[MachineEpisode]) -> list[str]:
    caveats: list[str] = []
    if coverage.metric_samples == 0:
        caveats.append("machine_metric_sample has no rows in this window")
    if coverage.gpu_samples == 0:
        caveats.append("machine_gpu_sample has no rows in this window")
    if coverage.network_samples == 0:
        caveats.append("machine_network_sample has no rows in this window")
    if coverage.service_samples == 0:
        caveats.append("machine_service_state has no rows in this window")
    if not episodes:
        caveats.append("no machine episodes crossed configured absolute or robust thresholds")
    return caveats


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_number(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float(data.get(key))
        if value is not None:
            return value
    return None


def _gap_caveats(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    else:
        parsed = value
    if not parsed:
        return ()
    return tuple(f"capture gap: {item}" for item in parsed)


__all__ = [
    "MachineEpisode",
    "MachineEpisodeAnalysis",
    "MachineEpisodeCoverage",
    "MachineEpisodeEvidence",
    "MachineEpisodeKindDefinition",
    "EPISODE_KIND_DEFINITIONS",
    "analyze_machine_episodes",
    "write_machine_episode_analysis",
]
