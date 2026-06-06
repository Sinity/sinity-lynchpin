"""Readiness report for machine-performance analysis claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_materialized_analysis_artifact, materialize_analysis_artifacts, save_json
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
    materialized_snapshot_count: int
    latest_materialized_refresh_id: str | None


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
    latest_materialized = conn.execute(
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
        materialized_snapshot_count=int(row[3]),
        latest_materialized_refresh_id=str(latest_materialized[0]) if latest_materialized else None,
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
        ("machine_below_export_handoff.json", "planned_window_count"),
        ("machine_context_windows.json", "window_count"),
        ("machine_work_observations.json", None),
        ("machine_analysis_feature_frames.json", "frame.row_count"),
        ("machine_mining.json", "cohort_count"),
        ("machine_dataset_diagnostics.json", "diagnostic_count"),
        ("machine_validation_design.json", "boundary_count"),
        ("machine_matched_designs.json", "design_count"),
        ("machine_negative_controls.json", "control_count"),
        ("machine_comparisons.json", "contrast_count"),
        ("machine_work_state_windows.json", "window_count"),
        ("command_performance_windows.json", "command_count"),
        ("machine_observational_deltas.json", "delta_count"),
        ("machine_attribution_candidates.json", "candidate_count"),
        ("machine_derivation_inventory.json", "ready_target_count"),
        ("machine_benchmark_plans.json", "plan_count"),
        ("machine_benchmark_manifest_bundle.json", "run_template_count"),
        ("machine_benchmark_preflight.json", "ready_run_count"),
        ("machine_experiment_manifest_diagnostics.json", "manifest_count"),
        ("machine_support_assessment.json", "assessment_count"),
        ("machine_mechanism_hypotheses.json", "mechanism_count"),
        ("machine_instrumentation_gaps.json", "gap_count"),
        ("machine_calibration_fixtures.json", "fixture_count"),
        ("machine_measurement_system.json", "check_count"),
        ("machine_attribution_claims.json", "claim_count"),
        ("machine_assumption_checks.json", "check_count"),
        ("devshell_performance.json", "command_count"),
        ("machine_observational_baselines.json", None),
        ("machine_experiment_claims.json", "run_count"),
        ("machine_analysis_materialization_report.json", "step_count"),
    )
    materialization = materialize_analysis_artifacts()
    result = []
    for artifact, count_path in artifacts:
        payload_dict = _load_machine_artifact(artifact, materialization=materialization)
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


def _load_machine_artifact(artifact: str, *, materialization: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload, _materialization = load_materialized_analysis_artifact(artifact, materialization=materialization)
    return payload if isinstance(payload, dict) else None


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
    support_artifact = artifact_map["machine_support_assessment.json"]
    preflight_artifact = artifact_map["machine_benchmark_preflight.json"]
    matched_artifact = artifact_map["machine_matched_designs.json"]
    negative_artifact = artifact_map["machine_negative_controls.json"]
    measurement_artifact = artifact_map["machine_measurement_system.json"]
    return [
        _dimension(
            "continuous_machine_telemetry",
            "stable" if metric.row_count >= 100 else ("limited" if metric.row_count else "missing"),
            (
                f"{metric.row_count} metric rows",
                f"span={_span(metric)}",
                f"{metric.materialized_snapshot_count} materialized snapshots",
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
            _pre_post_caveats(before_after, scope="current analysis window"),
        ),
        _dimension(
            "all_data_pre_post_bios_comparison",
            _pre_post_status(all_data_before_after),
            (
                f"boundary={bios_boundary.isoformat()}",
                f"before={all_data_before_after['before']} metric rows",
                f"after={all_data_before_after['after']} metric rows",
            ),
            _pre_post_caveats(all_data_before_after, scope="promoted machine substrate"),
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
        _devshell_nix_focus_dimension(devshell_artifact, experiment_artifact),
        _controlled_benchmark_claims_dimension(experiment, experiment_artifact),
        _benchmark_exportability_dimension(preflight_artifact),
        _support_gate_dimension(support_artifact),
        _natural_experiment_dimension(matched_artifact, negative_artifact, support_artifact),
        _measurement_system_dimension(measurement_artifact),
    ]


def _dimension(
    dimension: str,
    status: str,
    evidence: tuple[str, ...],
    caveats: tuple[str, ...],
) -> MachineReadinessDimension:
    return MachineReadinessDimension(dimension=dimension, status=status, evidence=evidence, caveats=caveats)


def _controlled_claim_count(artifact: MachineArtifactCoverage) -> int:
    payload = _artifact_payload(artifact)
    if payload is None:
        return 0
    value = payload.get("controlled_claim_count")
    return int(value) if isinstance(value, int) else 0


def _controlled_benchmark_claims_dimension(
    experiment: MachineTableCoverage,
    artifact: MachineArtifactCoverage,
) -> MachineReadinessDimension:
    controlled = _controlled_claim_count(artifact)
    return _dimension(
        "controlled_benchmark_claims",
        "stable" if experiment.row_count and controlled else "missing",
        (
            f"manifest_rows={experiment.row_count}",
            f"controlled_claim_count={controlled}",
        ),
        ("benchmark claims require randomized run manifests joined to telemetry by timestamp",)
        if not controlled
        else (),
    )


def _devshell_nix_focus_dimension(
    devshell_artifact: MachineArtifactCoverage,
    experiment_artifact: MachineArtifactCoverage,
) -> MachineReadinessDimension:
    payload = _artifact_payload(experiment_artifact) or {}
    structured_runs = _structured_internal_json_run_count(payload)
    devshell_count = devshell_artifact.primary_count or 0
    if structured_runs:
        status = "stable"
    elif devshell_count:
        status = "limited"
    else:
        status = "missing"
    caveats = []
    if devshell_count:
        caveats.append("devshell performance summaries are command-text classified")
    if not structured_runs:
        caveats.append("no parsed Nix internal-json benchmark phases are available")
    return _dimension(
        "devshell_nix_focus",
        status,
        (
            f"devshell_command_count={devshell_count}",
            f"structured_internal_json_run_count={structured_runs}",
        ),
        tuple(caveats),
    )


def _structured_internal_json_run_count(payload: dict[str, Any]) -> int:
    count = 0
    packs = payload.get("claim_packs") if isinstance(payload.get("claim_packs"), list) else []
    for row in packs:
        if not isinstance(row, dict) or row.get("claim_mode") != "controlled_benchmark":
            continue
        internal_json = row.get("internal_json") if isinstance(row.get("internal_json"), dict) else {}
        if internal_json.get("exists") is True and _int_value(internal_json.get("phase_count")) > 0:
            count += 1
    return count


def _benchmark_exportability_dimension(artifact: MachineArtifactCoverage) -> MachineReadinessDimension:
    payload = _artifact_payload(artifact) or {}
    run_count = _int_value(payload.get("run_count"))
    ready_run_count = _int_value(payload.get("ready_run_count"))
    issue_count = _int_value(payload.get("issue_count"))
    warning_count = _int_value(payload.get("warning_count"))
    if run_count == 0:
        status = "missing"
    elif issue_count == 0 and ready_run_count == run_count:
        status = "stable"
    elif ready_run_count > 0:
        status = "limited"
    else:
        status = "missing"
    caveats = []
    if run_count == 0:
        caveats.append("no controlled benchmark run templates exist")
    if issue_count:
        caveats.append("one or more run templates fail benchmark preflight")
    if warning_count:
        caveats.append("run templates still carry export-time warnings")
    return _dimension(
        "controlled_benchmark_exportability",
        status,
        (
            f"run_templates={ready_run_count}/{run_count} ready",
            f"preflight_issues={issue_count}",
            f"preflight_warnings={warning_count}",
        ),
        tuple(caveats),
    )


def _support_gate_dimension(artifact: MachineArtifactCoverage) -> MachineReadinessDimension:
    payload = _artifact_payload(artifact) or {}
    assessment_count = _int_value(payload.get("assessment_count"))
    refusal_count = _int_value(payload.get("refusal_count"))
    controlled = _int_value(payload.get("controlled_claim_count"))
    natural = _int_value(payload.get("natural_experiment_support_count"))
    ready_plans = _int_value(payload.get("ready_plan_count"))
    supported = controlled + natural
    if supported:
        status = "stable"
    elif assessment_count:
        status = "limited"
    else:
        status = "missing"
    caveats = []
    if supported == 0:
        caveats.append("no candidate currently passes the controlled or natural-experiment support gate")
    if refusal_count:
        caveats.append(f"{refusal_count} machine attribution candidates are explicitly refused")
    return _dimension(
        "causal_support_gate",
        status,
        (
            f"assessments={assessment_count}",
            f"controlled_supported={controlled}",
            f"natural_experiment_supported={natural}",
            f"refusals={refusal_count}",
            f"ready_benchmark_plans={ready_plans}",
        ),
        tuple(caveats),
    )


def _natural_experiment_dimension(
    matched_artifact: MachineArtifactCoverage,
    negative_artifact: MachineArtifactCoverage,
    support_artifact: MachineArtifactCoverage,
) -> MachineReadinessDimension:
    matched_payload = _artifact_payload(matched_artifact) or {}
    negative_payload = _artifact_payload(negative_artifact) or {}
    support_payload = _artifact_payload(support_artifact) or {}
    design_count = _int_value(matched_payload.get("design_count"))
    control_count = _int_value(negative_payload.get("control_count"))
    control_status = _status_counts(negative_payload.get("by_status"))
    failed_controls = control_status.get("failed", 0)
    passed_controls = control_status.get("passed", 0)
    selected_designs = _natural_support_design_ids(support_payload)
    selected_status = _control_status_for_designs(negative_payload, selected_designs)
    selected_failed = selected_status.get("failed", 0)
    selected_unavailable = selected_status.get("unavailable", 0)
    if design_count and control_count and selected_designs and selected_failed == 0:
        status = "stable"
    elif design_count or control_count:
        status = "limited"
    else:
        status = "missing"
    caveats = []
    if design_count == 0:
        caveats.append("no matched natural-experiment designs are available")
    if not selected_designs:
        caveats.append("no natural-experiment support-selected designs are available")
    if control_count == 0:
        caveats.append("no negative controls are available for natural-experiment designs")
    if selected_failed:
        caveats.append("one or more support-selected negative controls failed")
    elif failed_controls:
        caveats.append("some non-selected negative controls failed")
    if selected_unavailable:
        caveats.append("one or more support-selected negative controls are unavailable")
    return _dimension(
        "natural_experiment_identification",
        status,
        (
            f"matched_designs={design_count}",
            f"support_selected_designs={len(selected_designs)}",
            f"negative_controls={control_count}",
            f"negative_controls_passed={passed_controls}",
            f"negative_controls_failed={failed_controls}",
            f"support_selected_negative_controls_failed={selected_failed}",
            f"support_selected_negative_controls_unavailable={selected_unavailable}",
        ),
        tuple(caveats),
    )


def _natural_support_design_ids(payload: dict[str, Any]) -> set[str]:
    design_ids: set[str] = set()
    assessments = payload.get("assessments") if isinstance(payload.get("assessments"), list) else []
    for row in assessments:
        if not isinstance(row, dict) or row.get("support_level") != "natural_experiment":
            continue
        for source_id in row.get("source_ids", ()) if isinstance(row.get("source_ids"), list) else ():
            text = str(source_id)
            if text.startswith("machine-matched-design:"):
                design_ids.add(text)
    return design_ids


def _control_status_for_designs(payload: dict[str, Any], design_ids: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    controls = payload.get("controls") if isinstance(payload.get("controls"), list) else []
    for row in controls:
        if not isinstance(row, dict) or str(row.get("design_id") or "") not in design_ids:
            continue
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _measurement_system_dimension(artifact: MachineArtifactCoverage) -> MachineReadinessDimension:
    payload = _artifact_payload(artifact) or {}
    check_count = _int_value(payload.get("check_count"))
    by_status = _status_counts(payload.get("by_status"))
    passed = by_status.get("passed", 0)
    failed = by_status.get("failed", 0)
    limited = by_status.get("limited", 0)
    missing = by_status.get("missing", 0)
    if failed:
        status = "limited"
    elif passed >= 3:
        status = "stable"
    elif passed or limited:
        status = "limited"
    else:
        status = "missing"
    caveats = []
    if failed:
        caveats.append("one or more measurement-system diagnostics failed")
    if missing:
        caveats.append("one or more measurement-system diagnostics are missing")
    return _dimension(
        "measurement_system_diagnostics",
        status,
        (
            f"checks={check_count}",
            f"passed={passed}",
            f"limited={limited}",
            f"missing={missing}",
            f"failed={failed}",
        ),
        tuple(caveats),
    )


def _artifact_payload(artifact: MachineArtifactCoverage) -> dict[str, Any] | None:
    return _load_machine_artifact(artifact.artifact)


def _status_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _int_value(count) for key, count in value.items()}


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
    payload_dict = _load_machine_artifact(artifact.artifact) or {}
    attributed = _int_value(payload_dict.get("attributed_episode_count"))
    workload_attributed = _int_value(payload_dict.get("workload_resource_attributed_pressure_episode_count"))
    pressure = _int_value(payload_dict.get("pressure_episode_count"))
    captures = _int_value(payload_dict.get("capture_count"))
    live_store_indexes = _int_value(payload_dict.get("live_store_index_count"))
    live_first = payload_dict.get("live_store_first_observed_at")
    live_last = payload_dict.get("live_store_last_observed_at")
    if pressure == 0:
        status = "stable"
        caveats_list: list[str] = []
    else:
        ratio = (attributed + workload_attributed) / pressure
        status = "stable" if ratio >= 0.8 else "limited"
        caveats_list = []
        if status != "stable":
            caveats_list.append("most pressure episodes lack bounded below or workload resource attribution")
        if captures == 0:
            caveats_list.append("no bounded below captures are available for process/cgroup attribution")
        elif attributed == 0:
            caveats_list.append("bounded below captures do not overlap current machine pressure episodes")
    if live_store_indexes and attributed == 0:
        caveats_list.append("live below store exists but bounded exports or decoder output are missing for pressure episodes")
    return _dimension(
        "below_process_attribution",
        status,
        (
            f"bounded_below_capture_count={captures}",
            f"live_below_store_indexes={live_store_indexes}",
            f"live_below_store_span={live_first or 'none'}..{live_last or 'none'}",
            f"bounded_below_attributed_pressure_episodes={attributed}/{pressure}",
            f"workload_resource_attributed_pressure_episodes={workload_attributed}/{pressure}",
            f"combined_attributed_pressure_episodes={attributed + workload_attributed}/{pressure}",
            f"artifact_primary_count={artifact.primary_count or 0}",
        ),
        tuple(caveats_list),
    )


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int) else 0


def _pre_post_status(counts: dict[str, int]) -> str:
    if counts["before"] >= 100 and counts["after"] >= 100:
        return "stable"
    if counts["before"] and counts["after"]:
        return "limited"
    return "missing"


def _pre_post_caveats(counts: dict[str, int], *, scope: str) -> tuple[str, ...]:
    caveats = []
    if counts["before"] == 0:
        caveats.append(f"no pre-boundary machine_metric_sample rows in {scope}")
    if counts["after"] == 0:
        caveats.append(f"no post-boundary machine_metric_sample rows in {scope}")
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
