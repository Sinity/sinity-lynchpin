"""Manifest-backed machine experiment claim packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.controlled_benchmarks import (
    benchmark_readiness,
    bootstrap_delta_ci,
    selected_run_assignment_issues,
    stratified_bootstrap_delta_ci,
)
from lynchpin.analysis.machine.episodes import MachineEpisode, analyze_machine_episodes
from lynchpin.analysis.machine.nix_internal_json import summarize_internal_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path
from lynchpin.substrate.snapshots import best_materialized_refresh_id


@dataclass(frozen=True)
class ExperimentTelemetryWindow:
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    avg_load_1m: float | None
    p95_load_1m: float | None
    min_mem_avail_mb: int | None
    avg_io_psi_full: float | None
    gpu_pcie_regimes: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentEpisodeOverlap:
    kind: str
    host: str
    overlap_seconds: float
    severity: float
    confidence: float
    subject: str | None


@dataclass(frozen=True)
class MachineExperimentClaimPack:
    run_id: str
    run_group_id: str | None
    host: str
    workload: str
    claim_mode: str
    treatment_label: str
    cache_condition: str | None
    derivation_key: str | None
    started_at: datetime
    ended_at: datetime | None
    monotonic_started_ns: int | None
    monotonic_ended_ns: int | None
    duration_seconds: float | None
    exit_status: int | None
    execution_outcome: dict[str, Any]
    manifest_validation: dict[str, Any]
    command: tuple[str, ...]
    cwd: str | None
    measurement_context: dict[str, Any]
    nix_internal_json_path: str | None
    git_root: str | None
    git_head: str | None
    git_branch: str | None
    git_dirty: bool | None
    manifest_path: str
    telemetry: ExperimentTelemetryWindow
    internal_json: dict[str, Any]
    episodes: tuple[ExperimentEpisodeOverlap, ...]
    effect_estimates: tuple[dict[str, Any], ...]
    benchmark_readiness: dict[str, Any]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineExperimentClaims:
    run_count: int
    controlled_claim_count: int
    observational_claim_count: int
    claim_packs: list[MachineExperimentClaimPack]
    effect_estimates: list[dict[str, Any]]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_experiment_claims(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
    include_episodes: bool = True,
) -> MachineExperimentClaims:
    with connect(path or substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "machine_experiment_run",
                caller="machine_experiment_claims",
            )
        runs = _runs(conn, start=start, end=end, refresh_id=refresh_id)
        episodes = _episodes_for_runs(runs, path=path) if include_episodes else []
        packs = [_claim_pack(conn, run, episodes=episodes) for run in runs]
        estimates = _effect_estimates(packs)

    caveats: list[str] = []
    if refresh_id is None:
        caveats.append("machine_experiment_run has no promoted manifests")
    if not packs:
        caveats.append("no machine experiment manifests matched the analysis window")
    if not any(pack.claim_mode == "controlled_benchmark" for pack in packs):
        caveats.append("no manifest carries explicit randomization/control metadata; controlled benchmark claims are refused")
    return MachineExperimentClaims(
        run_count=len(packs),
        controlled_claim_count=sum(1 for pack in packs if pack.claim_mode == "controlled_benchmark"),
        observational_claim_count=sum(1 for pack in packs if pack.claim_mode != "controlled_benchmark"),
        claim_packs=packs,
        effect_estimates=estimates,
        caveats=sorted(dict.fromkeys(caveats)),
    )


def write_machine_experiment_claims(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
) -> MachineExperimentClaims:
    analysis = analyze_machine_experiment_claims(start=start, end=end, path=path, refresh_id=refresh_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _runs(conn: Any, *, start: date | None, end: date | None, refresh_id: str | None) -> list[dict[str, Any]]:
    if refresh_id is None:
        return []
    columns = _table_columns(conn, "machine_experiment_run")
    clauses = ["refresh_id = ?"]
    params: list[Any] = [refresh_id]
    if start is not None:
        clauses.append("CAST(started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(started_at AS DATE) <= ?")
        params.append(end)
    rows = conn.execute(
        f"""
        SELECT
            run_id, run_group_id, host, workload, command, cwd,
            started_at, ended_at, monotonic_started_ns, monotonic_ended_ns,
            exit_status, execution_outcome,
            service_profile, cache_profile, measurement_context, planned_treatment,
            nix_internal_json_path,
            git_root, git_head, git_branch, git_dirty, pre_state, post_state,
            notes,
            {_select_or_default(columns, "validation_status", "'unknown' AS validation_status")},
            {_select_or_default(columns, "validation_issues", "[]::VARCHAR[] AS validation_issues")},
            {_select_or_default(columns, "validation_warnings", "[]::VARCHAR[] AS validation_warnings")},
            {_select_or_default(columns, "manifest_validation", "'{}'::JSON AS manifest_validation")},
            manifest_path, refresh_id
        FROM machine_experiment_run
        WHERE {" AND ".join(clauses)}
        ORDER BY started_at, run_id
        """,
        params,
    ).fetchall()
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _table_columns(conn: Any, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _select_or_default(columns: set[str], column: str, default_expression: str) -> str:
    return column if column in columns else default_expression


def _episodes_for_runs(runs: list[dict[str, Any]], *, path: Path | None) -> list[MachineEpisode]:
    if not runs:
        return []
    starts = [run["started_at"].date() for run in runs]
    ends = [
        (run.get("ended_at") if run.get("ended_at") is not None else run["started_at"] + timedelta(minutes=5)).date()
        for run in runs
    ]
    return analyze_machine_episodes(start=min(starts), end=max(ends), path=path).episodes


def _claim_pack(conn: Any, run: dict[str, Any], *, episodes: list[MachineEpisode]) -> MachineExperimentClaimPack:
    started_at = run["started_at"]
    ended_at = run.get("ended_at")
    duration = (ended_at - started_at).total_seconds() if ended_at is not None and ended_at > started_at else None
    planned = _json_dict(run.get("planned_treatment"))
    readiness = benchmark_readiness(planned)
    telemetry = _telemetry_window(conn, run)
    internal_json = summarize_internal_json(_internal_json_path(run, readiness))
    assignment_issues = selected_run_assignment_issues(
        planned,
        payload_run_id=str(run["run_id"]),
        payload_run_group_id=str(run["run_group_id"]) if run.get("run_group_id") is not None else None,
    )
    validation_issues = tuple(_string_list(run.get("validation_issues")))
    claim_mode = _claim_mode(
        planned,
        internal_json.to_dict(),
        telemetry,
        assignment_issues=assignment_issues,
        validation_status=str(run.get("validation_status") or "unknown"),
        validation_issues=validation_issues,
    )
    overlaps = _episode_overlaps(run, episodes=episodes)
    caveats = _run_caveats(
        run,
        planned,
        telemetry,
        internal_json.to_dict(),
        duration,
        claim_mode,
        readiness,
        assignment_issues=assignment_issues,
    )
    return MachineExperimentClaimPack(
        run_id=str(run["run_id"]),
        run_group_id=readiness.run_group_id,
        host=str(run["host"]),
        workload=str(run["workload"]),
        claim_mode=claim_mode,
        treatment_label=_treatment_label(planned),
        cache_condition=_cache_condition(planned),
        derivation_key=_selected_run_field(planned, "derivation_key"),
        started_at=started_at,
        ended_at=ended_at,
        monotonic_started_ns=_int_or_none(run.get("monotonic_started_ns")),
        monotonic_ended_ns=_int_or_none(run.get("monotonic_ended_ns")),
        duration_seconds=round(duration, 3) if duration is not None else None,
        exit_status=run.get("exit_status"),
        execution_outcome=_json_dict(run.get("execution_outcome")),
        manifest_validation=_manifest_validation_payload(run),
        command=tuple(str(item) for item in (run.get("command") or [])),
        cwd=run.get("cwd"),
        measurement_context=_json_dict(run.get("measurement_context")),
        nix_internal_json_path=_internal_json_path(run, readiness),
        git_root=run.get("git_root"),
        git_head=run.get("git_head"),
        git_branch=run.get("git_branch"),
        git_dirty=run.get("git_dirty"),
        manifest_path=str(run["manifest_path"]),
        telemetry=telemetry,
        internal_json=internal_json.to_dict(),
        episodes=overlaps,
        effect_estimates=(),
        benchmark_readiness=readiness.to_dict(),
        caveats=tuple(caveats),
    )


def _telemetry_window(conn: Any, run: dict[str, Any]) -> ExperimentTelemetryWindow:
    started_at = run["started_at"]
    ended_at = run.get("ended_at")
    if ended_at is None or ended_at <= started_at:
        ended_at = started_at + timedelta(minutes=5)
    elif (ended_at - started_at).total_seconds() < 60:
        started_at = started_at - timedelta(seconds=30)
        ended_at = ended_at + timedelta(seconds=30)
    metric_rows = latest_machine_rows("machine_metric_sample")
    rows = conn.execute(
        f"""
        SELECT
            COUNT(*),
            MIN(observed_at),
            MAX(observed_at),
            AVG(load_1m),
            quantile_cont(load_1m, 0.95),
            MIN(mem_avail_mb),
            AVG(coalesce(io_psi_full_avg10, io_psi_full_avg60))
        FROM ({metric_rows})
        WHERE host = ? AND observed_at >= ? AND observed_at <= ?
        """,
        [run["host"], started_at, ended_at],
    ).fetchone()
    regimes = conn.execute(
        f"""
        SELECT gpu_pcie_gen, gpu_pcie_width, COUNT(*) AS n
        FROM ({metric_rows})
        WHERE host = ? AND observed_at >= ? AND observed_at <= ?
          AND gpu_pcie_gen IS NOT NULL AND gpu_pcie_width IS NOT NULL
        GROUP BY gpu_pcie_gen, gpu_pcie_width
        ORDER BY n DESC, gpu_pcie_gen DESC, gpu_pcie_width DESC
        """,
        [run["host"], started_at, ended_at],
    ).fetchall()
    return ExperimentTelemetryWindow(
        sample_count=int(rows[0]),
        first_observed_at=rows[1],
        last_observed_at=rows[2],
        avg_load_1m=_round(rows[3]),
        p95_load_1m=_round(rows[4]),
        min_mem_avail_mb=None if rows[5] is None else int(rows[5]),
        avg_io_psi_full=_round(rows[6]),
        gpu_pcie_regimes=tuple(f"gen{int(gen)}x{int(width)}" for gen, width, _ in regimes),
    )


def _episode_overlaps(run: dict[str, Any], *, episodes: list[MachineEpisode]) -> tuple[ExperimentEpisodeOverlap, ...]:
    started_at = run["started_at"]
    ended_at = run.get("ended_at")
    if ended_at is None or ended_at <= started_at:
        ended_at = started_at + timedelta(minutes=5)
    rows = []
    for episode in episodes:
        if episode.host != run["host"]:
            continue
        overlap = _overlap_seconds(started_at, ended_at, episode)
        if overlap <= 0:
            continue
        rows.append(ExperimentEpisodeOverlap(
            kind=episode.kind,
            host=episode.host,
            overlap_seconds=round(overlap, 3),
            severity=episode.severity,
            confidence=episode.confidence,
            subject=episode.subject,
        ))
    rows.sort(key=lambda row: (-row.overlap_seconds, -row.severity, row.kind))
    return tuple(rows)


def _run_caveats(
    run: dict[str, Any],
    planned: dict[str, Any],
    telemetry: ExperimentTelemetryWindow,
    internal_json: dict[str, Any],
    duration: float | None,
    claim_mode: str,
    readiness: Any,
    assignment_issues: tuple[str, ...],
) -> list[str]:
    caveats = ["manifest-backed claim pack; raw manifest remains the provenance source"]
    if claim_mode != "controlled_benchmark":
        caveats.append("observational manifest only; do not use controlled benchmark language")
    caveats.extend(f"controlled benchmark contract gap: {issue}" for issue in readiness.issues)
    caveats.extend(f"selected-run assignment gap: {issue}" for issue in assignment_issues)
    if run.get("validation_status") == "unknown":
        caveats.append("manifest validation status is unknown; substrate row predates validation columns")
    caveats.extend(f"manifest validation issue: {issue}" for issue in _string_list(run.get("validation_issues")))
    caveats.extend(f"manifest validation warning: {issue}" for issue in _string_list(run.get("validation_warnings")))
    if duration is None:
        caveats.append("manifest has no positive duration; telemetry join uses a five-minute inspection window")
    if telemetry.sample_count == 0:
        caveats.append("no machine telemetry samples overlap the run window")
    elif duration is not None and duration < 60:
        caveats.append("machine telemetry joined with cadence padding for short run")
    if not _has_complete_internal_json_phase(internal_json):
        caveats.append("internal-json has no complete timed phase")
    caveats.extend(f"internal-json caveat: {issue}" for issue in internal_json.get("caveats", ()))
    if run.get("git_dirty") is True:
        caveats.append("git checkout was dirty during run")
    if run.get("exit_status") not in (None, 0):
        caveats.append(f"run exited nonzero: {run.get('exit_status')}")
    return caveats


def _claim_mode(
    planned: dict[str, Any],
    internal_json: dict[str, Any],
    telemetry: ExperimentTelemetryWindow,
    *,
    assignment_issues: tuple[str, ...],
    validation_status: str,
    validation_issues: tuple[str, ...],
) -> str:
    return (
        "controlled_benchmark"
        if benchmark_readiness(planned).controlled
        and not assignment_issues
        and validation_status == "valid"
        and not validation_issues
        and internal_json.get("exists") is True
        and _has_internal_json_phase(internal_json)
        and telemetry.sample_count > 0
        else "manifest_observational"
    )


def _has_internal_json_phase(internal_json: dict[str, Any]) -> bool:
    return any(isinstance(phase, dict) for phase in internal_json.get("phases", ()))


def _has_complete_internal_json_phase(internal_json: dict[str, Any]) -> bool:
    return any(
        isinstance(phase, dict) and phase.get("status") == "complete"
        for phase in internal_json.get("phases", ())
    )


def _treatment_label(planned: dict[str, Any]) -> str:
    selected = planned.get("selected_run")
    if isinstance(selected, dict):
        for key in ("treatment_label", "treatment"):
            if selected.get(key) is not None:
                return f"{key}={selected[key]}"
    for key in ("treatment_label", "treatment", "turbo", "trigger", "purpose", "capture_kind"):
        if planned.get(key) is not None:
            return f"{key}={planned[key]}"
    return "unspecified"


def _cache_condition(planned: dict[str, Any]) -> str | None:
    selected = planned.get("selected_run")
    if isinstance(selected, dict) and selected.get("cache_condition") is not None:
        return str(selected["cache_condition"])
    for key in ("cache_condition", "cache_profile"):
        if planned.get(key) is not None:
            return str(planned[key])
    benchmark = planned.get("controlled_benchmark") or planned.get("benchmark")
    if isinstance(benchmark, dict) and benchmark.get("cache_condition") is not None:
        return str(benchmark["cache_condition"])
    return None


def _selected_run_field(planned: dict[str, Any], key: str) -> str | None:
    selected = planned.get("selected_run")
    if isinstance(selected, dict) and selected.get(key) is not None:
        return str(selected[key])
    return None


def _effect_estimates(packs: list[MachineExperimentClaimPack]) -> list[dict[str, Any]]:
    by_group: dict[str, list[MachineExperimentClaimPack]] = {}
    for pack in packs:
        if pack.claim_mode != "controlled_benchmark" or pack.run_group_id is None:
            continue
        if pack.duration_seconds is None:
            continue
        by_group.setdefault(pack.run_group_id, []).append(pack)

    estimates: list[dict[str, Any]] = []
    for run_group_id, rows in sorted(by_group.items()):
        readiness = rows[0].benchmark_readiness
        control_label = str(readiness.get("control_label") or "control")
        treatment_label = str(readiness.get("treatment_label") or "treatment")
        control = tuple(
            float(row.duration_seconds)
            for row in rows
            if row.duration_seconds is not None and row.treatment_label.endswith(f"={control_label}")
        )
        treatment = tuple(
            float(row.duration_seconds)
            for row in rows
            if row.duration_seconds is not None and row.treatment_label.endswith(f"={treatment_label}")
        )
        seed = sum(ord(ch) for ch in run_group_id)
        estimate = _stratified_effect_estimate(
            rows,
            control_label=control_label,
            treatment_label=treatment_label,
            seed=seed,
        ) or bootstrap_delta_ci(
            control,
            treatment,
            metric="duration_seconds",
            control_label=control_label,
            treatment_label=treatment_label,
            seed=seed,
        )
        if estimate is None:
            continue
        estimates.append({"run_group_id": run_group_id, **estimate.to_dict()})
    return estimates


def _stratified_effect_estimate(
    rows: list[MachineExperimentClaimPack],
    *,
    control_label: str,
    treatment_label: str,
    seed: int,
) -> Any:
    control_by_stratum: dict[str, list[float]] = {}
    treatment_by_stratum: dict[str, list[float]] = {}
    for row in rows:
        if row.duration_seconds is None:
            continue
        stratum = _estimator_stratum(row)
        if stratum is None:
            continue
        if row.treatment_label.endswith(f"={control_label}"):
            control_by_stratum.setdefault(stratum, []).append(float(row.duration_seconds))
        elif row.treatment_label.endswith(f"={treatment_label}"):
            treatment_by_stratum.setdefault(stratum, []).append(float(row.duration_seconds))

    complete = {
        stratum
        for stratum in set(control_by_stratum) | set(treatment_by_stratum)
        if control_by_stratum.get(stratum) and treatment_by_stratum.get(stratum)
    }
    if not complete:
        return None
    return stratified_bootstrap_delta_ci(
        {key: tuple(value) for key, value in control_by_stratum.items()},
        {key: tuple(value) for key, value in treatment_by_stratum.items()},
        metric="duration_seconds",
        control_label=control_label,
        treatment_label=treatment_label,
        seed=seed,
    )


def _estimator_stratum(row: MachineExperimentClaimPack) -> str | None:
    if row.cache_condition is None or row.derivation_key is None:
        return None
    return f"cache={row.cache_condition}|derivation={row.derivation_key}"


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    return [str(item) for item in decoded] if isinstance(decoded, list) else [str(value)]


def _manifest_validation_payload(run: dict[str, Any]) -> dict[str, Any]:
    payload = _json_dict(run.get("manifest_validation"))
    if payload:
        return payload
    return {
        "valid": True if run.get("validation_status") == "valid" else False if run.get("validation_status") == "invalid" else None,
        "issues": _string_list(run.get("validation_issues")),
        "warnings": _string_list(run.get("validation_warnings")),
    }


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _internal_json_path(run: dict[str, Any], readiness: Any) -> str | None:
    value = run.get("nix_internal_json_path")
    if value is not None:
        return str(value)
    return readiness.internal_json_path


def _overlap_seconds(started_at: datetime, ended_at: datetime, episode: MachineEpisode) -> float:
    left = max(started_at, episode.started_at)
    right = min(ended_at, episode.ended_at)
    return max(0.0, (right - left).total_seconds())


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
