"""Attribute machine episodes with bounded ``below`` process/cgroup captures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.below import DEFAULT_STABILITY_ROOT, BelowAnalysis, BelowEntitySummary, analyze_below_exports
from lynchpin.analysis.machine.episodes import MachineEpisode, analyze_machine_episodes
from lynchpin.core.parse import as_local


PRESSURE_EPISODE_KINDS = frozenset({"load_pressure", "cpu_saturation", "memory_pressure", "io_pressure", "blocked_task_pressure"})


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
class BelowAttributionAnalysis:
    episode_count: int
    attributed_episode_count: int
    pressure_episode_count: int
    unattributed_pressure_episode_count: int
    capture_count: int
    attributions: list[BelowEpisodeAttribution]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_below_attribution(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    top_n: int = 5,
    max_attributions: int = 500,
) -> BelowAttributionAnalysis:
    """Join machine episodes to bounded below export windows.

    The result enriches episodes with candidate process/cgroup contributors
    from matching below captures. It does not mutate the original episode
    evidence and does not treat below summaries as proof of root cause.
    """
    episode_analysis = analyze_machine_episodes(start=start, end=end, path=path)
    below = analyze_below_exports(root=root, top_n=max(top_n, 1))
    captures = [capture for capture in below.system if capture.first_observed_at and capture.last_observed_at]

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

    attributions.sort(key=lambda row: (-row.overlap_seconds, -row.severity, row.episode_started_at, row.episode_kind, row.capture_id))
    caveats = [*episode_analysis.caveats, *below.caveats]
    if len(attributions) > max_attributions:
        attributions = attributions[:max_attributions]
        caveats.append(f"below attributions truncated to top {max_attributions} rows by overlap and severity")
    if not captures:
        caveats.append("no bounded below capture windows available for episode attribution")
    if not attributions:
        caveats.append("no machine episodes overlapped bounded below capture windows")

    pressure_episodes = [episode for episode in episode_analysis.episodes if episode.kind in PRESSURE_EPISODE_KINDS]
    unattributed_pressure = [
        episode
        for episode in pressure_episodes
        if (episode.kind, episode.started_at, episode.ended_at, episode.subject) not in attributed_keys
    ]
    if unattributed_pressure:
        caveats.append(f"{len(unattributed_pressure)} pressure episodes have no overlapping bounded below capture")

    return BelowAttributionAnalysis(
        episode_count=len(episode_analysis.episodes),
        attributed_episode_count=len(attributed_keys),
        pressure_episode_count=len(pressure_episodes),
        unattributed_pressure_episode_count=len(unattributed_pressure),
        capture_count=len(captures),
        attributions=attributions,
        caveats=sorted(dict.fromkeys(caveats)),
    )


def write_below_attribution_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    root: Path = DEFAULT_STABILITY_ROOT,
    top_n: int = 5,
    max_attributions: int = 500,
) -> BelowAttributionAnalysis:
    analysis = analyze_below_attribution(
        start=start,
        end=end,
        path=path,
        root=root,
        top_n=top_n,
        max_attributions=max_attributions,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


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


def _overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    left = max(_aware(a_start), _aware(b_start))
    right = min(_aware(a_end), _aware(b_end))
    return max(0.0, (right - left).total_seconds())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return as_local(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)
