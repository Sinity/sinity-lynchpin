"""Attribute machine episodes with bounded ``below`` process/cgroup captures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.below import (
    DEFAULT_LIVE_BELOW_STORE,
    DEFAULT_STABILITY_ROOT,
    BelowAnalysis,
    BelowEntitySummary,
    BelowWindowExport,
    analyze_below_exports,
    export_live_below_window,
    failed_below_exports,
)
from lynchpin.analysis.machine.episodes import MachineEpisode, analyze_machine_episodes
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.core.parse import as_local
from lynchpin.substrate.connection import connect, substrate_path


PRESSURE_EPISODE_KINDS = frozenset({"load_pressure", "cpu_saturation", "memory_pressure", "swap_pressure", "io_pressure", "system_stall", "blocked_task_pressure"})


@dataclass(frozen=True)
class BelowContributor:
    kind: str
    key: str
    sample_count: int
    avg_cpu_pct: float | None
    max_cpu_pct: float | None
    max_rss_mb: float | None
    max_mem_total_mb: float | None


@dataclass(frozen=True)
class BelowEpisodeAttribution:
    episode_kind: str
    host: str
    episode_started_at: datetime
    episode_ended_at: datetime
    severity: float
    confidence: float
    capture_id: str
    capture_started_at: datetime
    capture_ended_at: datetime
    overlap_seconds: float
    top_processes: tuple[BelowContributor, ...]
    top_cgroups: tuple[BelowContributor, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadResourceAttribution:
    episode_kind: str
    host: str
    episode_started_at: datetime
    episode_ended_at: datetime
    severity: float
    confidence: float
    work_source: str
    work_source_id: str
    project: str | None
    live_stage: str | None
    command: tuple[str, ...]
    work_started_at: datetime
    work_ended_at: datetime
    overlap_seconds: float
    process_cpu_usage_avg: float | None
    process_memory_usage_max_mb: float | None
    root_process_cpu_usage_avg: float | None
    root_process_memory_usage_max_mb: float | None
    shared_nix_daemon_cpu_usage_avg: float | None
    shared_nix_build_slice_cpu_usage_avg: float | None
    shared_background_slice_cpu_usage_avg: float | None
    host_cpu_pressure_some_avg10_max: float | None
    host_io_pressure_some_avg10_max: float | None
    host_io_pressure_full_avg10_max: float | None
    host_memory_pressure_some_avg10_max: float | None
    host_memory_pressure_full_avg10_max: float | None
    process_count_max: int | None
    resource_sample_count: int | None
    attribution_basis: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class BelowPressureWindowPlan:
    episode_kind: str
    host: str
    episode_started_at: datetime
    episode_ended_at: datetime
    severity: float
    confidence: float
    begin: datetime
    end: datetime
    capture_id: str
    reason: str


@dataclass(frozen=True)
class BelowPressureWindowExport:
    plan: BelowPressureWindowPlan
    export: BelowWindowExport | None


@dataclass(frozen=True)
class BelowAttributionAnalysis:
    episode_count: int
    attributed_episode_count: int
    pressure_episode_count: int
    unattributed_pressure_episode_count: int
    workload_resource_attributed_pressure_episode_count: int
    residual_unattributed_pressure_episode_count: int
    capture_count: int
    live_store_index_count: int
    live_store_first_observed_at: datetime | None
    live_store_last_observed_at: datetime | None
    attributions: list[BelowEpisodeAttribution]
    workload_resource_attributions: list[WorkloadResourceAttribution]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _EpisodeBounds:
    episode: MachineEpisode
    started_at: datetime
    ended_at: datetime
    key: tuple[str, datetime, datetime, str | None]


@dataclass(frozen=True)
class _WorkloadBounds:
    row: dict[str, Any]
    started_at: datetime
    ended_at: datetime
    basis: tuple[str, ...]


def analyze_below_attribution(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    top_n: int = 5,
    max_attributions: int = 500,
) -> BelowAttributionAnalysis:
    """Join machine episodes to bounded below export windows.

    The result enriches episodes with candidate process/cgroup contributors
    from matching below captures. It does not mutate the original episode
    evidence and does not treat below summaries as proof of root cause.
    """
    episode_analysis = analyze_machine_episodes(start=start, end=end, path=path)
    below = analyze_below_exports(root=root, live_store=live_store, top_n=max(top_n, 1))
    captures = [capture for capture in below.system if capture.first_observed_at and capture.last_observed_at]
    workload_rows = tuple(
        _WorkloadBounds(
            row=row,
            started_at=_aware(row["started_at"]),
            ended_at=_aware(row["ended_at"]),
            basis=_workload_basis(row),
        )
        for row in _workload_resource_windows(start=start, end=end, path=path)
    )
    pressure_episode_rows = tuple(
        _EpisodeBounds(
            episode=episode,
            started_at=_aware(episode.started_at),
            ended_at=_aware(episode.ended_at),
            key=(episode.kind, episode.started_at, episode.ended_at, episode.subject),
        )
        for episode in episode_analysis.episodes
        if episode.kind in PRESSURE_EPISODE_KINDS
    )

    attributions: list[BelowEpisodeAttribution] = []
    attributed_keys: set[tuple[str, datetime, datetime, str | None]] = set()
    for episode in episode_analysis.episodes:
        for capture in captures:
            overlap = _overlap_seconds(
                episode.started_at,
                episode.ended_at,
                capture.first_observed_at,
                capture.last_observed_at,
            )
            if overlap <= 0:
                continue
            attributed_keys.add((episode.kind, episode.started_at, episode.ended_at, episode.subject))
            attributions.append(_attribution_row(episode, capture.capture_id, capture.first_observed_at, capture.last_observed_at, overlap, below, top_n=top_n))

    workload_attributions: list[WorkloadResourceAttribution] = []
    workload_attributed_keys: set[tuple[str, datetime, datetime, str | None]] = set()
    for episode_row in pressure_episode_rows:
        episode = episode_row.episode
        for row in workload_rows:
            if row.row.get("host") != episode.host:
                continue
            left = max(episode_row.started_at, row.started_at)
            right = min(episode_row.ended_at, row.ended_at)
            overlap = max(0.0, (right - left).total_seconds())
            if overlap <= 0:
                continue
            if not row.basis:
                continue
            workload_attributed_keys.add(episode_row.key)
            workload_attributions.append(_workload_attribution_row(episode, row.row, overlap_seconds=overlap, basis=row.basis))

    attributions.sort(key=lambda row: (-row.overlap_seconds, -row.severity, row.episode_started_at, row.episode_kind, row.capture_id))
    workload_attributions.sort(key=lambda row: (-row.overlap_seconds, -row.severity, row.episode_started_at, row.work_source_id))
    caveats = [*episode_analysis.caveats, *below.caveats]
    if len(attributions) > max_attributions:
        attributions = attributions[:max_attributions]
        caveats.append(f"below attributions truncated to top {max_attributions} rows by overlap and severity")
    if len(workload_attributions) > max_attributions:
        workload_attributions = workload_attributions[:max_attributions]
        caveats.append(f"workload resource attributions truncated to top {max_attributions} rows by overlap and severity")
    if not captures:
        caveats.append("no bounded below capture windows available for episode attribution")
    if not attributions:
        caveats.append("no machine episodes overlapped bounded below capture windows")

    pressure_episodes = [row.episode for row in pressure_episode_rows]
    unattributed_pressure = [
        episode
        for episode in pressure_episodes
        if (episode.kind, episode.started_at, episode.ended_at, episode.subject) not in attributed_keys
    ]
    residual_unattributed_pressure = [
        episode
        for episode in pressure_episodes
        if (episode.kind, episode.started_at, episode.ended_at, episode.subject) not in attributed_keys
        and (episode.kind, episode.started_at, episode.ended_at, episode.subject) not in workload_attributed_keys
    ]
    if unattributed_pressure:
        caveats.append(f"{len(unattributed_pressure)} pressure episodes have no overlapping bounded below capture")
    if workload_attributions:
        caveats.append(
            "workload resource attribution uses promoted work-observation telemetry; it is narrower than below process/cgroup capture"
        )
    if (
        pressure_episode_rows
        and not attributions
        and below.live_store.index_count
        and below.live_store.first_observed_at
        and below.live_store.last_observed_at
    ):
        first_pressure = min(row.started_at for row in pressure_episode_rows)
        last_pressure = max(row.ended_at for row in pressure_episode_rows)
        live_overlap = _overlap_seconds(
            first_pressure,
            last_pressure,
            below.live_store.first_observed_at,
            below.live_store.last_observed_at,
        )
        if live_overlap > 0:
            caveats.append(
                "live below store overlaps pressure episodes; bounded CSV export or store decoder is the missing attribution step"
            )
    if residual_unattributed_pressure:
        caveats.append(
            f"{len(residual_unattributed_pressure)} pressure episodes lack both bounded below and workload resource attribution"
        )

    return BelowAttributionAnalysis(
        episode_count=len(episode_analysis.episodes),
        attributed_episode_count=len(attributed_keys),
        pressure_episode_count=len(pressure_episodes),
        unattributed_pressure_episode_count=len(unattributed_pressure),
        workload_resource_attributed_pressure_episode_count=len(workload_attributed_keys),
        residual_unattributed_pressure_episode_count=len(residual_unattributed_pressure),
        capture_count=len(captures),
        live_store_index_count=below.live_store.index_count,
        live_store_first_observed_at=below.live_store.first_observed_at,
        live_store_last_observed_at=below.live_store.last_observed_at,
        attributions=attributions,
        workload_resource_attributions=workload_attributions,
        caveats=sorted(dict.fromkeys(caveats)),
    )


def write_below_attribution_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    top_n: int = 5,
    max_attributions: int = 500,
) -> BelowAttributionAnalysis:
    analysis = analyze_below_attribution(
        start=start,
        end=end,
        path=path,
        root=root,
        live_store=live_store,
        top_n=top_n,
        max_attributions=max_attributions,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def plan_below_windows_for_pressure_episodes(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    limit: int = 10,
    padding_seconds: int = 60,
    min_duration_seconds: int = 120,
    include_existing_captures: bool = False,
    retry_failed_exports: bool = False,
) -> list[BelowPressureWindowPlan]:
    episode_analysis = analyze_machine_episodes(start=start, end=end, path=path)
    below = analyze_below_exports(root=root, live_store=live_store, top_n=1)
    captures = [capture for capture in below.system if capture.first_observed_at and capture.last_observed_at]
    failed_capture_ids = set() if retry_failed_exports else {row.capture_id for row in failed_below_exports(root=root)}
    pressure = sorted(
        (episode for episode in episode_analysis.episodes if episode.kind in PRESSURE_EPISODE_KINDS),
        key=lambda episode: (-episode.severity, episode.started_at, episode.kind),
    )
    plans: list[BelowPressureWindowPlan] = []
    planned_windows: list[tuple[datetime, datetime]] = []
    for episode in pressure:
        if not include_existing_captures and any(
            _overlap_seconds(episode.started_at, episode.ended_at, capture.first_observed_at, capture.last_observed_at) > 0
            for capture in captures
        ):
            continue
        begin, window_end = _below_export_bounds(
            episode.started_at,
            episode.ended_at,
            padding_seconds=padding_seconds,
            min_duration_seconds=min_duration_seconds,
        )
        capture_id = _pressure_capture_id(episode, begin, window_end)
        if capture_id in failed_capture_ids:
            continue
        if (
            below.live_store.first_observed_at
            and below.live_store.last_observed_at
            and _overlap_seconds(begin, window_end, below.live_store.first_observed_at, below.live_store.last_observed_at)
            <= 0
        ):
            continue
        if any(_overlap_seconds(begin, window_end, planned_begin, planned_end) > 0 for planned_begin, planned_end in planned_windows):
            continue
        plans.append(
            BelowPressureWindowPlan(
                episode_kind=episode.kind,
                host=episode.host,
                episode_started_at=episode.started_at,
                episode_ended_at=episode.ended_at,
                severity=episode.severity,
                confidence=episode.confidence,
                begin=begin,
                end=window_end,
                capture_id=capture_id,
                reason="pressure episode lacks bounded below attribution",
            )
        )
        planned_windows.append((begin, window_end))
        if len(plans) >= limit:
            break
    return plans


def export_below_windows_for_pressure_episodes(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    live_store: Path = DEFAULT_LIVE_BELOW_STORE,
    limit: int = 10,
    padding_seconds: int = 60,
    min_duration_seconds: int = 120,
    top_n: int = 20,
    timeout_s: int = 60,
    dry_run: bool = True,
) -> list[BelowPressureWindowExport]:
    plans = plan_below_windows_for_pressure_episodes(
        start=start,
        end=end,
        path=path,
        root=root,
        live_store=live_store,
        limit=limit,
        padding_seconds=padding_seconds,
        min_duration_seconds=min_duration_seconds,
    )
    exports: list[BelowPressureWindowExport] = []
    for plan in plans:
        export = None
        if not dry_run:
            export = export_live_below_window(
                root=root,
                begin=plan.begin,
                end=plan.end,
                duration=None,
                capture_id=plan.capture_id,
                top_n=top_n,
                timeout_s=timeout_s,
            )
        exports.append(BelowPressureWindowExport(plan=plan, export=export))
    return exports


def _attribution_row(
    episode: MachineEpisode,
    capture_id: str,
    capture_started_at: datetime,
    capture_ended_at: datetime,
    overlap_seconds: float,
    below: BelowAnalysis,
    *,
    top_n: int,
) -> BelowEpisodeAttribution:
    top_processes = _contributors(
        (row for row in below.top_processes if row.capture_id == capture_id),
        start=episode.started_at,
        end=episode.ended_at,
        top_n=top_n,
    )
    top_cgroups = _contributors(
        (row for row in below.top_cgroups if row.capture_id == capture_id),
        start=episode.started_at,
        end=episode.ended_at,
        top_n=top_n,
    )
    caveats = ["below attribution is observational; it narrows candidate contributors but does not prove root cause"]
    if episode.kind in PRESSURE_EPISODE_KINDS and not top_processes and not top_cgroups:
        caveats.append("pressure episode overlaps below system capture but has no process/cgroup contributor rows")
    return BelowEpisodeAttribution(
        episode_kind=episode.kind,
        host=episode.host,
        episode_started_at=episode.started_at,
        episode_ended_at=episode.ended_at,
        severity=episode.severity,
        confidence=episode.confidence,
        capture_id=capture_id,
        capture_started_at=capture_started_at,
        capture_ended_at=capture_ended_at,
        overlap_seconds=round(overlap_seconds, 3),
        top_processes=top_processes,
        top_cgroups=top_cgroups,
        caveats=tuple(caveats),
    )


def _contributors(
    rows: Iterable[BelowEntitySummary],
    *,
    start: datetime,
    end: datetime,
    top_n: int,
) -> tuple[BelowContributor, ...]:
    ordered = sorted(
        (row for row in rows if _entity_overlaps(row, start=start, end=end)),
        key=lambda row: (
            row.max_cpu_pct or 0.0,
            row.avg_cpu_pct or 0.0,
            row.max_rss_mb or row.max_mem_total_mb or 0.0,
        ),
        reverse=True,
    )[:top_n]
    return tuple(
        BelowContributor(
            kind=row.kind,
            key=row.key,
            sample_count=row.sample_count,
            avg_cpu_pct=row.avg_cpu_pct,
            max_cpu_pct=row.max_cpu_pct,
            max_rss_mb=row.max_rss_mb,
            max_mem_total_mb=row.max_mem_total_mb,
        )
        for row in ordered
    )


def _entity_overlaps(row: BelowEntitySummary, *, start: datetime, end: datetime) -> bool:
    if row.first_observed_at is None or row.last_observed_at is None:
        return True
    if row.first_observed_at == row.last_observed_at:
        point = _aware(row.first_observed_at)
        return _aware(start) <= point <= _aware(end)
    return _overlap_seconds(start, end, row.first_observed_at, row.last_observed_at) > 0


def _workload_resource_windows(
    *,
    start: date | None,
    end: date | None,
    path: Path | None,
) -> list[dict[str, Any]]:
    clauses = ["ended_at IS NOT NULL"]
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(started_at AS DATE) <= ?")
        params.append(end)
    rows_sql = latest_machine_rows("work_observation")
    with connect(path or substrate_path(), read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT
                source, source_id, project, command, started_at, ended_at,
                duration_s, host, live_stage,
                process_cpu_usage_avg, process_memory_usage_max_mb,
                root_process_cpu_usage_avg, root_process_memory_usage_max_mb,
                shared_nix_daemon_cpu_usage_avg,
                shared_nix_build_slice_cpu_usage_avg,
                shared_background_slice_cpu_usage_avg,
                host_cpu_pressure_some_avg10_max,
                host_io_pressure_some_avg10_max,
                host_io_pressure_full_avg10_max,
                host_memory_pressure_some_avg10_max,
                host_memory_pressure_full_avg10_max,
                process_count_max, resource_sample_count
            FROM ({rows_sql})
            WHERE {" AND ".join(clauses)}
              AND (
                process_cpu_usage_avg IS NOT NULL
                OR process_memory_usage_max_mb IS NOT NULL
                OR root_process_cpu_usage_avg IS NOT NULL
                OR root_process_memory_usage_max_mb IS NOT NULL
                OR shared_nix_daemon_cpu_usage_avg IS NOT NULL
                OR shared_nix_build_slice_cpu_usage_avg IS NOT NULL
                OR shared_background_slice_cpu_usage_avg IS NOT NULL
                OR host_cpu_pressure_some_avg10_max IS NOT NULL
                OR host_io_pressure_some_avg10_max IS NOT NULL
                OR host_io_pressure_full_avg10_max IS NOT NULL
                OR host_memory_pressure_some_avg10_max IS NOT NULL
                OR host_memory_pressure_full_avg10_max IS NOT NULL
              )
            ORDER BY started_at, source_id
            """,
            params,
        ).fetchall()
        columns = [desc[0] for desc in (conn.description or [])]
    return [_normalize_workload_row(dict(zip(columns, row, strict=True))) for row in rows]


def _normalize_workload_row(row: dict[str, Any]) -> dict[str, Any]:
    started_at = row["started_at"]
    ended_at = row.get("ended_at")
    if ended_at is None:
        ended_at = started_at
    row["started_at"] = started_at
    row["ended_at"] = ended_at
    return row


def _workload_basis(row: dict[str, Any]) -> tuple[str, ...]:
    basis = []
    for key in (
        "process_cpu_usage_avg",
        "process_memory_usage_max_mb",
        "root_process_cpu_usage_avg",
        "root_process_memory_usage_max_mb",
        "shared_nix_daemon_cpu_usage_avg",
        "shared_nix_build_slice_cpu_usage_avg",
        "shared_background_slice_cpu_usage_avg",
        "host_cpu_pressure_some_avg10_max",
        "host_io_pressure_some_avg10_max",
        "host_io_pressure_full_avg10_max",
        "host_memory_pressure_some_avg10_max",
        "host_memory_pressure_full_avg10_max",
    ):
        if row.get(key) is not None:
            basis.append(key)
    return tuple(basis)


def _workload_attribution_row(
    episode: MachineEpisode,
    row: dict[str, Any],
    *,
    overlap_seconds: float,
    basis: tuple[str, ...],
) -> WorkloadResourceAttribution:
    return WorkloadResourceAttribution(
        episode_kind=episode.kind,
        host=episode.host,
        episode_started_at=episode.started_at,
        episode_ended_at=episode.ended_at,
        severity=episode.severity,
        confidence=episode.confidence,
        work_source=str(row["source"]),
        work_source_id=str(row["source_id"]),
        project=str(row["project"]) if row.get("project") else None,
        live_stage=str(row["live_stage"]) if row.get("live_stage") else None,
        command=tuple(str(item) for item in (row.get("command") or ())),
        work_started_at=row["started_at"],
        work_ended_at=row["ended_at"],
        overlap_seconds=round(overlap_seconds, 3),
        process_cpu_usage_avg=_float_or_none(row.get("process_cpu_usage_avg")),
        process_memory_usage_max_mb=_float_or_none(row.get("process_memory_usage_max_mb")),
        root_process_cpu_usage_avg=_float_or_none(row.get("root_process_cpu_usage_avg")),
        root_process_memory_usage_max_mb=_float_or_none(row.get("root_process_memory_usage_max_mb")),
        shared_nix_daemon_cpu_usage_avg=_float_or_none(row.get("shared_nix_daemon_cpu_usage_avg")),
        shared_nix_build_slice_cpu_usage_avg=_float_or_none(row.get("shared_nix_build_slice_cpu_usage_avg")),
        shared_background_slice_cpu_usage_avg=_float_or_none(row.get("shared_background_slice_cpu_usage_avg")),
        host_cpu_pressure_some_avg10_max=_float_or_none(row.get("host_cpu_pressure_some_avg10_max")),
        host_io_pressure_some_avg10_max=_float_or_none(row.get("host_io_pressure_some_avg10_max")),
        host_io_pressure_full_avg10_max=_float_or_none(row.get("host_io_pressure_full_avg10_max")),
        host_memory_pressure_some_avg10_max=_float_or_none(row.get("host_memory_pressure_some_avg10_max")),
        host_memory_pressure_full_avg10_max=_float_or_none(row.get("host_memory_pressure_full_avg10_max")),
        process_count_max=_int_or_none(row.get("process_count_max")),
        resource_sample_count=_int_or_none(row.get("resource_sample_count")),
        attribution_basis=basis,
        caveats=(
            "workload resource attribution is observational; it identifies overlapping measured workload resource use, not root cause",
        ),
    )


def _overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    left = max(_aware(a_start), _aware(b_start))
    right = min(_aware(a_end), _aware(b_end))
    return max(0.0, (right - left).total_seconds())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return as_local(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _below_export_bounds(
    started_at: datetime,
    ended_at: datetime,
    *,
    padding_seconds: int,
    min_duration_seconds: int,
) -> tuple[datetime, datetime]:
    begin = as_local(started_at) - timedelta(seconds=padding_seconds)
    end = as_local(ended_at) + timedelta(seconds=padding_seconds)
    duration = (end - begin).total_seconds()
    if duration < min_duration_seconds:
        extra = (min_duration_seconds - duration) / 2
        begin -= timedelta(seconds=extra)
        end += timedelta(seconds=extra)
    return begin, end


def _pressure_capture_id(episode: MachineEpisode, begin: datetime, end: datetime) -> str:
    return (
        f"pressure-{episode.kind}-"
        f"{begin.strftime('%Y%m%dT%H%M%S')}-"
        f"{end.strftime('%Y%m%dT%H%M%S')}"
    )
