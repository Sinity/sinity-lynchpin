"""Machine telemetry and machine-analysis MCP tools.

Do not enable postponed annotations in this module: FastMCP inspects function
annotations while registering @app.tool functions.
"""

import json
from pathlib import Path
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, json_safe as _json_safe


def _analysis_artifact(name: str) -> dict[str, Any] | None:
    from lynchpin.core.io import resolve_analysis_path

    path = Path(resolve_analysis_path(name))
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def _required_analysis_artifact(name: str) -> dict[str, Any]:
    from lynchpin.core.io import resolve_analysis_path

    path = Path(resolve_analysis_path(name))
    payload = _analysis_artifact(name)
    if payload is None:
        raise FileNotFoundError(
            f"required machine analysis artifact is missing or malformed: {path}"
        )
    return payload


def _timestamp_filter(
    row: dict[str, Any],
    *,
    start: str | None,
    end: str | None,
    start_key: str,
    end_key: str,
) -> bool:
    if not start and not end:
        return True
    row_start = str(row.get(start_key) or "")
    row_end = str(row.get(end_key) or "")
    row_day_start = row_start[:10]
    row_day_end = row_end[:10] or row_day_start
    if start and row_day_end < start:
        return False
    if end and row_day_start > end:
        return False
    return True


@app.tool()
def machine_metrics_daily(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily machine telemetry rollup from the machine_metric_sample table."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_metric_daily

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_metric_sample")
            if refresh_id is None:
                return []

        rows = load_machine_metric_daily(
            conn,
            refresh_id=refresh_id,
            start=_date.fromisoformat(start) if start else None,
            end=_date.fromisoformat(end) if end else None,
            host=host,
        )

    return [
        {
            "date": _json_safe(row[0]),
            "host": row[1],
            "samples": row[2],
            "avg_cpu_package_w": row[3],
            "max_cpu_package_w": row[4],
            "avg_gpu_power_w": row[5],
            "max_gpu_power_w": row[6],
            "avg_io_psi_some_avg10": row[7],
            "max_io_psi_some_avg10": row[8],
            "avg_latency_oversleep_ms": row[9],
            "max_latency_oversleep_ms": row[10],
            "max_dstate_task_count": row[11],
        }
        for row in rows
    ]


@app.tool()
def machine_metrics_by_context(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily machine-metric series segmented by the Layer-1 context vector.

    Splits each day by software_revision (the NixOS generation active at each
    sample, via an ASOF join on activated_at <= observed_at) and hardware_regime
    (GPU PCIe link gen/width), aggregating CPU/GPU power and CPU/IO pressure.
    Use this to compare a metric across generation boundaries or PCIe link
    regimes. ``generation`` is null for samples predating any activation record
    (missing generation telemetry is reported as null, never imputed).
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_metric_series_by_context

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_metric_sample")
            if refresh_id is None:
                return []
        generations_refresh_id = best_refresh_id(conn, "sinnix_generation")

        rows = load_machine_metric_series_by_context(
            conn,
            refresh_id=refresh_id,
            generations_refresh_id=generations_refresh_id,
            start=_date.fromisoformat(start) if start else None,
            end=_date.fromisoformat(end) if end else None,
            host=host,
        )

    return [
        {
            "day": _json_safe(row[0]),
            "generation": row[1],
            "sinnix_revision": row[2],
            "gpu_pcie_gen": row[3],
            "gpu_pcie_width": row[4],
            "samples": row[5],
            "avg_cpu_package_w": row[6],
            "avg_gpu_power_w": row[7],
            "avg_io_psi_full_avg10": row[8],
            "max_io_psi_full_avg10": row[9],
            "avg_cpu_psi_some_avg60": row[10],
        }
        for row in rows
    ]


@app.tool()
def machine_episodes(
    start: str | None = None,
    end: str | None = None,
    kind: str | None = None,
    host: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read typed machine episodes from the materialized analysis artifact."""
    payload = _required_analysis_artifact("machine_episode_analysis.json")
    episodes = [row for row in payload.get("episodes", []) if isinstance(row, dict)]
    rows = [
        row
        for row in episodes
        if (kind is None or row.get("kind") == kind)
        and (host is None or row.get("host") == host)
        and _timestamp_filter(row, start=start, end=end, start_key="started_at", end_key="ended_at")
    ]
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("kind") or ""), str(row.get("host") or "")))
    return rows[:max(limit, 0)]


@app.tool()
def machine_context_windows(
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
    has_episodes: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read machine/work context windows from the materialized artifact."""
    payload = _required_analysis_artifact("machine_context_windows.json")
    windows = [row for row in payload.get("windows", []) if isinstance(row, dict)]
    rows = []
    for row in windows:
        projects = row.get("projects") if isinstance(row.get("projects"), list) else []
        episode_count = int(row.get("episode_count") or 0)
        if project is not None and project not in projects:
            continue
        if source is not None and row.get("source") != source:
            continue
        if has_episodes is not None and bool(episode_count) is not has_episodes:
            continue
        if not _timestamp_filter(row, start=start, end=end, start_key="started_at", end_key="ended_at"):
            continue
        rows.append(row)
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("source") or ""), str(row.get("window_id") or "")))
    return rows[:max(limit, 0)]


@app.tool()
def machine_below_attributions(
    start: str | None = None,
    end: str | None = None,
    episode_kind: str | None = None,
    capture_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read below process/cgroup attribution rows for machine episodes."""
    payload = _analysis_artifact("machine_below_attribution.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "attributions": []}
    attributions = [row for row in payload.get("attributions", []) if isinstance(row, dict)]
    rows = [
        row
        for row in attributions
        if (episode_kind is None or row.get("episode_kind") == episode_kind)
        and (capture_id is None or row.get("capture_id") == capture_id)
        and _timestamp_filter(row, start=start, end=end, start_key="episode_started_at", end_key="episode_ended_at")
    ]
    rows.sort(key=lambda row: (-float(row.get("overlap_seconds") or 0), -float(row.get("severity") or 0), str(row.get("episode_started_at") or "")))
    summary = {
        "episode_count": payload.get("episode_count"),
        "attributed_episode_count": payload.get("attributed_episode_count"),
        "pressure_episode_count": payload.get("pressure_episode_count"),
        "unattributed_pressure_episode_count": payload.get("unattributed_pressure_episode_count"),
        "capture_count": payload.get("capture_count"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "attributions": rows[:max(limit, 0)]}


@app.tool()
def machine_observational_baselines(
    dimension: str | None = None,
    key: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read observational machine telemetry baselines."""
    payload = _analysis_artifact("machine_observational_baselines.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    families = {
        "hour": payload.get("by_hour", []),
        "source": payload.get("by_source", []),
        "hardware": payload.get("by_hardware_regime", []),
        "work_context": payload.get("work_context", []),
        "daily_signal": payload.get("daily_signals", []),
        "era_comparison": payload.get("era_comparisons", []),
    }
    selected = [dimension] if dimension else list(families)
    rows: list[dict[str, Any]] = []
    for family in selected:
        for row in families.get(family, []):
            if not isinstance(row, dict):
                continue
            row_key = row.get("key") or row.get("metric") or row.get("boundary")
            if key is not None and row_key != key:
                continue
            rows.append({"dimension": family, **row})
    summary = {
        "generated_for": payload.get("generated_for", {}),
        "caveats": payload.get("caveats", []),
        "family_counts": {name: len(rows) if isinstance(rows, list) else 0 for name, rows in families.items()},
    }
    return {"summary": summary, "rows": rows[:max(limit, 0)]}


@app.tool()
def machine_experiment_claims(
    claim_mode: str | None = None,
    workload: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read manifest-backed machine experiment claim packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "claim_packs": []}
    packs = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    rows = [
        row
        for row in packs
        if (claim_mode is None or row.get("claim_mode") == claim_mode)
        and (workload is None or row.get("workload") == workload)
    ]
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("run_id") or "")))
    summary = {
        "run_count": payload.get("run_count"),
        "controlled_claim_count": payload.get("controlled_claim_count"),
        "observational_claim_count": payload.get("observational_claim_count"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "claim_packs": rows[:max(limit, 0)]}


@app.tool()
def machine_service_state_summary(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    unit: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Summarize sampled systemd/user-unit state from machine_service_state."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_service_state_summary

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_service_state")
            if refresh_id is None:
                return []

        rows = load_machine_service_state_summary(
            conn,
            refresh_id=refresh_id,
            start=_date.fromisoformat(start) if start else None,
            end=_date.fromisoformat(end) if end else None,
            host=host,
            unit=unit,
        )

    return [
        {
            "host": row[0],
            "unit": row[1],
            "scope": row[2],
            "samples": row[3],
            "active_samples": row[4],
            "max_memory_current_bytes": row[5],
            "max_cpu_usage_nsec": row[6],
            "max_io_read_bytes": row[7],
            "max_io_write_bytes": row[8],
            "first_observed_at": _json_safe(row[9]),
            "last_observed_at": _json_safe(row[10]),
        }
        for row in rows
    ]


@app.tool()
def borg_drill_history(
    limit: int = 50,
    status: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return random-archive deep-verify drill history written by
    sinnix-borg-drill (weekly oneshot, one row per repo per invocation).

    The drill complements borg's repository-only check by sampling
    chunk-content integrity via `borg check --verify-data`. Use this
    tool to confirm the integrity story for each repo: every row is
    one archive whose chunks were re-read and verified end-to-end.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_borg_drill_runs, load_borg_drill_summary

    with connect(substrate_path(), read_only=True) as conn:
        rows = load_borg_drill_runs(conn, limit=limit, status=status, repo=repo)
        summary_row = load_borg_drill_summary(conn)

    return {
        "summary": {
            "total": int(summary_row[0]) if summary_row else 0,
            "ok": int(summary_row[1]) if summary_row else 0,
            "failed": int(summary_row[2]) if summary_row else 0,
            "last_started_at": _json_safe(summary_row[3]) if summary_row else None,
            "filters": {"status": status, "repo": repo},
        },
        "rows": [
            {
                "repo": row[0],
                "archive": row[1],
                "started_at": _json_safe(row[2]),
                "ended_at": _json_safe(row[3]),
                "duration_s": int(row[4]),
                "exit_code": int(row[5]),
                "status": row[6],
                "within_days": int(row[7]),
            }
            for row in rows
        ],
    }


@app.tool()
def sinnix_generation_history(
    limit: int = 50,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Return the NixOS-generation activation history captured by the
    sinnix `lynchpinGenerationLog` activation script.

    Each row corresponds to one `nixos-rebuild switch` activation and
    carries {host, generation, activated_at, store_path, sinnix_revision,
    nixos_label}. Use the activated_at column to join against
    machine_metric_sample.observed_at — the latest activated_at <= a
    sample's observed_at is the generation that produced the sample.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_sinnix_generation_rows

    with connect(substrate_path(), read_only=True) as conn:
        rows = load_sinnix_generation_rows(conn, limit=limit, host=host)
    return [
        {
            "host": row[0],
            "generation": row[1],
            "activated_at": _json_safe(row[2]),
            "store_path": row[3],
            "sinnix_revision": row[4],
            "nixos_label": row[5],
        }
        for row in rows
    ]


@app.tool()
def machine_bufferbloat_summary(
    start: str | None = None,
    end: str | None = None,
    interface: str | None = None,
) -> dict[str, Any]:
    """Return per-day bufferbloat measurements from `machine_network_sample`.

    The bufferbloat probe (`bufferbloatIntervalSec=1800` on
    machine-telemetry) samples the ICMP RTT distribution to 8.8.8.8 by
    default; the result lands in the `bloat` JSON column. Most rows
    carry no `bloat` (the cadence is much slower than the per-sample
    network probe); rows that do carry it expose `avg_ms`, `min_ms`,
    `max_ms`, `loss`, `status`, `ip`.

    This tool aggregates those rows by day per interface and returns
    {sample_count, avg_ms_{p50,p95,max}, loss_{p50,p95,max}} so callers
    can spot regression windows without re-implementing the JSON
    extraction. With limited data the percentile estimates are weak —
    use `sample_count` to weight conclusions.
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_bufferbloat_daily

    with connect(substrate_path(), read_only=True) as conn:
        rows = load_bufferbloat_daily(
            conn,
            start=_date.fromisoformat(start) if start else None,
            end=_date.fromisoformat(end) if end else None,
            interface=interface,
        )

    return {
        "summary": {
            "row_count": len(rows),
            "filters": {"start": start, "end": end, "interface": interface},
        },
        "rows": [
            {
                "day": _json_safe(row[0]),
                "interface": row[1],
                "sample_count": int(row[2]),
                "avg_ms_p50": _round(row[3]),
                "avg_ms_p95": _round(row[4]),
                "avg_ms_max": _round(row[5]),
                "loss_p50": _round(row[6]),
                "loss_p95": _round(row[7]),
                "loss_max": _round(row[8]),
            }
            for row in rows
        ],
    }


def _round(value: Any, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


@app.tool()
def machine_gap_summary(
    threshold_pct: float | None = None,
) -> dict[str, Any]:
    """Return per-(table, gap_code) share of recent telemetry rows that
    recorded each code, plus any code exceeding the regression threshold.

    Reads the materialized ``machine_gap_summary.json`` artifact produced
    by the daily refresh DAG; use ``threshold_pct`` to re-filter the
    ``regressions`` list to a stricter share than what the artifact was
    computed at.
    """
    payload = _analysis_artifact("machine_gap_summary.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "counts": [], "regressions": []}

    counts = [row for row in payload.get("counts", []) if isinstance(row, dict)]
    regressions = [row for row in payload.get("regressions", []) if isinstance(row, dict)]
    if threshold_pct is not None:
        regressions = [r for r in regressions if float(r.get("share_pct") or 0) >= threshold_pct]

    summary = {
        "generated_for": payload.get("generated_for", {}),
        "generated_at_utc": payload.get("generated_at_utc"),
        "count_total": len(counts),
        "regression_count": len(regressions),
        "effective_threshold_pct": threshold_pct
        if threshold_pct is not None
        else payload.get("generated_for", {}).get("regression_pct"),
    }
    return {"summary": summary, "counts": counts, "regressions": regressions}
