"""Readiness report for machine-performance analysis claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.core.io import load_analysis_artifact, save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path


DEFAULT_BIOS_BOUNDARY = date(2026, 5, 12)
MACHINE_TABLES = (
    "machine_metric_sample",
    "machine_gpu_sample",
    "machine_service_state",
    "machine_network_sample",
    "machine_experiment_run",
)


@dataclass(frozen=True)
class MachineTableCoverage:
    table: str
    row_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    refresh_count: int
    latest_refresh_id: str | None


@dataclass(frozen=True)
class MachineArtifactCoverage:
    artifact: str
    present: bool
    generated_at_utc: str | None
    primary_count: int | None


@dataclass(frozen=True)
class MachineReadinessDimension:
    dimension: str
    status: str
    evidence: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineAnalysisReadiness:
    generated_for: dict[str, Any]
    tables: list[MachineTableCoverage]
    artifacts: list[MachineArtifactCoverage]
    dimensions: list[MachineReadinessDimension]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_analysis_readiness(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    bios_boundary: date = DEFAULT_BIOS_BOUNDARY,
) -> MachineAnalysisReadiness:
    with connect(path or substrate_path(), read_only=True) as conn:
        tables = [_table_coverage(conn, table, start=start, end=end) for table in MACHINE_TABLES]
        before_after = _before_after_counts(conn, bios_boundary=bios_boundary, start=start, end=end)
        all_data_before_after = _before_after_counts(conn, bios_boundary=bios_boundary, start=None, end=None)
        network_interfaces = _network_interfaces(conn, start=start, end=end)

    artifacts = _artifact_coverages()
    dimensions = _dimensions(
        tables=tables,
        artifacts=artifacts,
        before_after=before_after,
        all_data_before_after=all_data_before_after,
        network_interfaces=network_interfaces,
        bios_boundary=bios_boundary,
    )
    caveats = _caveats(dimensions)
    return MachineAnalysisReadiness(
        generated_for={
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "bios_boundary": bios_boundary.isoformat(),
        "pre_post_scope": "selected_window_and_all_data",
    },
        tables=tables,
        artifacts=artifacts,
        dimensions=dimensions,
        caveats=caveats,
    )


def write_machine_analysis_readiness(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    bios_boundary: date = DEFAULT_BIOS_BOUNDARY,
) -> MachineAnalysisReadiness:
    analysis = analyze_machine_analysis_readiness(
        start=start,
        end=end,
        path=path,
        bios_boundary=bios_boundary,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _window_clause(start: date | None, end: date | None, column: str) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append(f"CAST({column} AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append(f"CAST({column} AS DATE) <= ?")
        params.append(end)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _time_column(table: str) -> str:
    return "started_at" if table == "machine_experiment_run" else "observed_at"


def _table_coverage(conn: Any, table: str, *, start: date | None, end: date | None) -> MachineTableCoverage:
    time_column = _time_column(table)
    where, params = _window_clause(start, end, time_column)
    rows_sql = latest_machine_rows(table)
    row = conn.execute(
        f"""
        SELECT count(*), min({time_column}), max({time_column}), count(DISTINCT refresh_id)
        FROM ({rows_sql})
        {where}
        """,
        params,
    ).fetchone()
    latest_refresh = conn.execute(
        f"""
        SELECT refresh_id
        FROM ({rows_sql})
        {where}
        GROUP BY refresh_id
        ORDER BY max(materialized_at) DESC, count(*) DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return MachineTableCoverage(
        table=table,
        row_count=int(row[0]),
        first_observed_at=row[1],
        last_observed_at=row[2],
        refresh_count=int(row[3]),
        latest_refresh_id=str(latest_refresh[0]) if latest_refresh else None,
    )


def _before_after_counts(
    conn: Any,
    *,
    bios_boundary: date,
    start: date | None,
    end: date | None,
) -> dict[str, int]:
    where, params = _window_clause(start, end, "observed_at")
    metric_rows = latest_machine_rows("machine_metric_sample")
    prefix = f"{where} AND " if where else "WHERE "
    before = conn.execute(
        f"SELECT count(*) FROM ({metric_rows}) {prefix}CAST(observed_at AS DATE) < ?",
        [*params, bios_boundary],
    ).fetchone()[0]
    after = conn.execute(
        f"SELECT count(*) FROM ({metric_rows}) {prefix}CAST(observed_at AS DATE) >= ?",
        [*params, bios_boundary],
    ).fetchone()[0]
    return {"before": int(before), "after": int(after)}


def _network_interfaces(conn: Any, *, start: date | None, end: date | None) -> dict[str, int]:
    where, params = _window_clause(start, end, "observed_at")
    network_rows = latest_machine_rows("machine_network_sample")
    rows = conn.execute(
        f"""
        SELECT interface, count(*)
        FROM ({network_rows})
        {where}
        GROUP BY interface
        ORDER BY count(*) DESC, interface
        """,
        params,
    ).fetchall()
    return {str(interface): int(count) for interface, count in rows}


def _artifact_coverages() -> list[MachineArtifactCoverage]:
    artifacts = (
        ("machine_telemetry_analysis.json", "coverage.sample_count"),
        ("machine_episode_analysis.json", "episode_count"),
        ("machine_below_analysis.json", "window_count"),
        ("machine_below_attribution.json", "attributed_episode_count"),
        ("machine_context_windows.json", "window_count"),
        ("machine_work_state_windows.json", "window_count"),
        ("command_performance_windows.json", "command_count"),
        ("machine_observational_deltas.json", "delta_count"),
        ("devshell_performance.json", "command_count"),
        ("machine_observational_baselines.json", None),
        ("machine_experiment_claims.json", "run_count"),
    )
    result = []
    for artifact, count_path in artifacts:
        payload_dict = load_analysis_artifact(artifact)
        result.append(
            MachineArtifactCoverage(
                artifact=artifact,
                present=payload_dict is not None,
                generated_at_utc=str(payload_dict.get("generated_at_utc"))
                if payload_dict is not None and payload_dict.get("generated_at_utc")
                else None,
                primary_count=_primary_count(payload_dict, count_path) if payload_dict is not None else None,
            )
        )
    return result


def _primary_count(payload: object, count_path: str | None) -> int | None:
    if not isinstance(payload, dict) or count_path is None:
        return None
    value: object = payload
    for part in count_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return int(value) if isinstance(value, int) else None


def _dimensions(
    *,
    tables: list[MachineTableCoverage],
    artifacts: list[MachineArtifactCoverage],
    before_after: dict[str, int],
    all_data_before_after: dict[str, int],
    network_interfaces: dict[str, int],
    bios_boundary: date,
) -> list[MachineReadinessDimension]:
    table_map = {row.table: row for row in tables}
    artifact_map = {row.artifact: row for row in artifacts}
    metric = table_map["machine_metric_sample"]
    experiment = table_map["machine_experiment_run"]
    command_artifact = artifact_map["command_performance_windows.json"]
    devshell_artifact = artifact_map["devshell_performance.json"]
    experiment_artifact = artifact_map["machine_experiment_claims.json"]
    return [
        _dimension(
            "continuous_machine_telemetry",
            "stable" if metric.row_count >= 100 else ("limited" if metric.row_count else "missing"),
            (
                f"{metric.row_count} metric rows",
                f"span={_span(metric)}",
                f"{metric.refresh_count} refresh ids",
            ),
            () if metric.row_count >= 100 else ("too few metric rows for robust observational analysis",),
        ),
        _dimension(
            "window_pre_post_bios_comparison",
            _pre_post_status(before_after),
            (
                f"boundary={bios_boundary.isoformat()}",
                f"before={before_after['before']} metric rows",
                f"after={before_after['after']} metric rows",
            ),
            _pre_post_caveats(before_after),
        ),
        _dimension(
            "all_data_pre_post_bios_comparison",
            _pre_post_status(all_data_before_after),
            (
                f"boundary={bios_boundary.isoformat()}",
                f"before={all_data_before_after['before']} metric rows",
                f"after={all_data_before_after['after']} metric rows",
            ),
            _pre_post_caveats(all_data_before_after),
        ),
        _dimension(
            "network_telemetry",
            _sample_count_status(sum(network_interfaces.values())),
            tuple(f"{iface}={count}" for iface, count in network_interfaces.items()) or ("no promoted network rows",),
            _network_caveats(sum(network_interfaces.values())),
        ),
        _below_attribution_dimension(artifact_map["machine_below_attribution.json"]),
        _dimension(
            "command_outcome_matching",
            "stable" if (command_artifact.primary_count or 0) > 0 else "missing",
            (f"command_count={command_artifact.primary_count or 0}",),
            () if (command_artifact.primary_count or 0) > 0 else ("Atuin command outcome joins are absent",),
        ),
        _dimension(
            "devshell_nix_focus",
            "limited" if (devshell_artifact.primary_count or 0) > 0 else "missing",
            (f"devshell_command_count={devshell_artifact.primary_count or 0}",),
            ("command-text classification only; structured Nix logs are needed for phase attribution",),
        ),
        _dimension(
            "controlled_benchmark_claims",
            "stable" if experiment.row_count and _controlled_claim_count(experiment_artifact) else "missing",
            (
                f"manifest_rows={experiment.row_count}",
                f"controlled_claim_count={_controlled_claim_count(experiment_artifact)}",
            ),
            ("benchmark claims require randomized run manifests joined to telemetry by timestamp",)
            if not _controlled_claim_count(experiment_artifact)
            else (),
        ),
    ]


def _dimension(
    dimension: str,
    status: str,
    evidence: tuple[str, ...],
    caveats: tuple[str, ...],
) -> MachineReadinessDimension:
    return MachineReadinessDimension(dimension=dimension, status=status, evidence=evidence, caveats=caveats)


def _controlled_claim_count(artifact: MachineArtifactCoverage) -> int:
    payload = load_analysis_artifact(artifact.artifact)
    if payload is None:
        return 0
    value = payload.get("controlled_claim_count")
    return int(value) if isinstance(value, int) else 0


def _sample_count_status(count: int) -> str:
    if count >= 100:
        return "stable"
    if count:
        return "limited"
    return "missing"


def _network_caveats(row_count: int) -> tuple[str, ...]:
    if row_count == 0:
        return ("network analysis cannot separate local host pressure from network path issues",)
    if row_count < 100:
        return ("too few network probe rows for robust network-path analysis",)
    return ()


def _below_attribution_dimension(artifact: MachineArtifactCoverage) -> MachineReadinessDimension:
    payload_dict = load_analysis_artifact(artifact.artifact) or {}
    attributed = _int_value(payload_dict.get("attributed_episode_count"))
    pressure = _int_value(payload_dict.get("pressure_episode_count"))
    if pressure == 0:
        status = "stable"
        caveats: tuple[str, ...] = ()
    else:
        ratio = attributed / pressure
        status = "stable" if ratio >= 0.8 else "limited"
        caveats = () if status == "stable" else (
            "most pressure episodes lack bounded below process/cgroup attribution",
        )
    return _dimension(
        "below_process_attribution",
        status,
        (
            f"attributed_pressure_episodes={attributed}/{pressure}",
            f"artifact_primary_count={artifact.primary_count or 0}",
        ),
        caveats,
    )


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int) else 0


def _pre_post_status(counts: dict[str, int]) -> str:
    if counts["before"] >= 100 and counts["after"] >= 100:
        return "stable"
    if counts["before"] and counts["after"]:
        return "limited"
    return "missing"


def _pre_post_caveats(counts: dict[str, int]) -> tuple[str, ...]:
    caveats = []
    if counts["before"] == 0:
        caveats.append("no pre-boundary machine_metric_sample rows in this window")
    if counts["after"] == 0:
        caveats.append("no post-boundary machine_metric_sample rows in this window")
    if 0 < counts["before"] < 100 or 0 < counts["after"] < 100:
        caveats.append("one side has fewer than 100 samples; compare only as weak observational context")
    if not caveats:
        caveats.append("observational before/after comparison still needs workload controls")
    return tuple(caveats)


def _span(row: MachineTableCoverage) -> str:
    if row.first_observed_at is None or row.last_observed_at is None:
        return "none"
    return f"{row.first_observed_at.isoformat()}..{row.last_observed_at.isoformat()}"


def _caveats(dimensions: list[MachineReadinessDimension]) -> list[str]:
    caveats: list[str] = []
    for dimension in dimensions:
        if dimension.status != "stable":
            caveats.append(f"{dimension.dimension}: {dimension.status}")
        caveats.extend(dimension.caveats)
    return sorted(dict.fromkeys(caveats))
