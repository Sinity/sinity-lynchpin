"""Manifest-backed machine experiment claim packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.core.io import save_json
from lynchpin.analysis.machine.episodes import MachineEpisode, analyze_machine_episodes
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.mcp.tools._utils import best_refresh_id
from lynchpin.substrate.connection import connect, substrate_path


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
    host: str
    workload: str
    claim_mode: str
    treatment_label: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    exit_status: int | None
    command: tuple[str, ...]
    cwd: str | None
    git_root: str | None
    git_head: str | None
    git_branch: str | None
    git_dirty: bool | None
    manifest_path: str
    telemetry: ExperimentTelemetryWindow
    episodes: tuple[ExperimentEpisodeOverlap, ...]
    effect_estimates: tuple[dict[str, Any], ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineExperimentClaims:
    run_count: int
    controlled_claim_count: int
    observational_claim_count: int
    claim_packs: list[MachineExperimentClaimPack]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_experiment_claims(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
) -> MachineExperimentClaims:
    with connect(path or substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_experiment_run")
        runs = _runs(conn, start=start, end=end, refresh_id=refresh_id)
        episodes = _episodes_for_runs(runs, path=path)
        packs = [_claim_pack(conn, run, episodes=episodes) for run in runs]

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
            run_id, host, workload, command, cwd, started_at, ended_at,
            exit_status, service_profile, cache_profile, planned_treatment,
            git_root, git_head, git_branch, git_dirty, pre_state, post_state,
            notes, manifest_path, refresh_id
        FROM machine_experiment_run
        WHERE {" AND ".join(clauses)}
        ORDER BY started_at, run_id
        """,
        params,
    ).fetchall()
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in rows]


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
    claim_mode = _claim_mode(planned)
    telemetry = _telemetry_window(conn, run)
    overlaps = _episode_overlaps(run, episodes=episodes)
    caveats = _run_caveats(run, planned, telemetry, duration, claim_mode)
    return MachineExperimentClaimPack(
        run_id=str(run["run_id"]),
        host=str(run["host"]),
        workload=str(run["workload"]),
        claim_mode=claim_mode,
        treatment_label=_treatment_label(planned),
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=round(duration, 3) if duration is not None else None,
        exit_status=run.get("exit_status"),
        command=tuple(str(item) for item in (run.get("command") or [])),
        cwd=run.get("cwd"),
        git_root=run.get("git_root"),
        git_head=run.get("git_head"),
        git_branch=run.get("git_branch"),
        git_dirty=run.get("git_dirty"),
        manifest_path=str(run["manifest_path"]),
        telemetry=telemetry,
        episodes=overlaps,
        effect_estimates=(),
        caveats=tuple(caveats),
    )


def _telemetry_window(conn: Any, run: dict[str, Any]) -> ExperimentTelemetryWindow:
    started_at = run["started_at"]
    ended_at = run.get("ended_at")
    if ended_at is None or ended_at <= started_at:
        ended_at = started_at + timedelta(minutes=5)
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
    duration: float | None,
    claim_mode: str,
) -> list[str]:
    caveats = ["manifest-backed claim pack; raw manifest remains the provenance source"]
    if claim_mode != "controlled_benchmark":
        caveats.append("observational manifest only; do not use controlled benchmark language")
    if not _has_randomization(planned):
        caveats.append("manifest lacks randomized run order or control/treatment matrix")
    if duration is None:
        caveats.append("manifest has no positive duration; telemetry join uses a five-minute inspection window")
    if telemetry.sample_count == 0:
        caveats.append("no machine telemetry samples overlap the run window")
    if run.get("git_dirty") is True:
        caveats.append("git checkout was dirty during run")
    if run.get("exit_status") not in (None, 0):
        caveats.append(f"run exited nonzero: {run.get('exit_status')}")
    return caveats


def _claim_mode(planned: dict[str, Any]) -> str:
    return "controlled_benchmark" if _has_randomization(planned) and _has_control(planned) else "manifest_observational"


def _has_randomization(planned: dict[str, Any]) -> bool:
    return planned.get("randomized") is True or any(
        key in planned for key in ("randomization", "randomized_order", "run_manifest", "assignment_seed")
    )


def _has_control(planned: dict[str, Any]) -> bool:
    return any(key in planned for key in ("control", "control_label", "treatment", "treatment_label", "matrix"))


def _treatment_label(planned: dict[str, Any]) -> str:
    for key in ("treatment_label", "treatment", "turbo", "trigger", "purpose", "capture_kind"):
        if planned.get(key) is not None:
            return f"{key}={planned[key]}"
    return "unspecified"


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


def _overlap_seconds(started_at: datetime, ended_at: datetime, episode: MachineEpisode) -> float:
    left = max(started_at, episode.started_at)
    right = min(ended_at, episode.ended_at)
    return max(0.0, (right - left).total_seconds())


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
