"""Episode detection across trajectory days.

Identifies multi-day episodes of sustained activity — e.g. "sinex coding sprint",
"research binge", "admin stall" — that are not calendar-aligned.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Optional, Sequence

from .day import TrajectoryDay

if TYPE_CHECKING:
    from .anomaly import TrajectoryAnomaly


@dataclass(frozen=True)
class TrajectoryEpisode:
    episode_id: str
    label: str
    start_date: date
    end_date: date
    days: int
    active_seconds: float
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    mode_distribution: dict[str, float]
    project_distribution: dict[str, float]
    trigger: str  # "project_shift", "mode_shift", "intensity_change"
    confidence: float
    day_count_with_dominant: int

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "label": self.label,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "days": self.days,
            "active_seconds": round(self.active_seconds, 3),
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "mode_distribution": {k: round(v, 3) for k, v in self.mode_distribution.items()},
            "project_distribution": {k: round(v, 3) for k, v in self.project_distribution.items()},
            "trigger": self.trigger,
            "confidence": round(self.confidence, 3),
            "day_count_with_dominant": self.day_count_with_dominant,
        }


def _episode_id(start: date, end: date, label: str) -> str:
    payload = f"{start.isoformat()}|{end.isoformat()}|{label}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _compose_label(mode: Optional[str], project: Optional[str], topic: Optional[str] = None) -> str:
    parts: list[str] = []
    if project:
        parts.append(project)
    if topic and topic != (project or "").lower():
        parts.append(topic)
    if mode:
        parts.append(mode)
    if not parts:
        return "mixed activity"
    return " ".join(parts)


def detect_episodes(
    days: Sequence[TrajectoryDay],
    *,
    min_days: int = 2,
    mode_threshold: float = 0.4,
    anomalies: Sequence[TrajectoryAnomaly] | None = None,
) -> list[TrajectoryEpisode]:
    """Detect episodes of sustained activity mode or project dominance.

    An episode starts when a mode or project exceeds ``mode_threshold``
    proportion of active time for ``min_days`` consecutive days,
    and ends when dominance breaks.
    """
    if len(days) < min_days:
        return []

    sorted_days = sorted(days, key=lambda d: d.date)
    episodes: list[TrajectoryEpisode] = []

    # Sliding window: track consecutive days sharing a dominant mode, project, or topic
    run_start = 0
    while run_start < len(sorted_days):
        seed_day = sorted_days[run_start]
        seed_mode = seed_day.dominant_mode
        seed_project = seed_day.dominant_project
        seed_topic = seed_day.dominant_topic

        if not seed_mode and not seed_project and not seed_topic:
            run_start += 1
            continue

        # Extend run forward
        run_end = run_start + 1
        while run_end < len(sorted_days):
            candidate = sorted_days[run_end]
            mode_match = seed_mode and candidate.dominant_mode == seed_mode
            project_match = seed_project and candidate.dominant_project == seed_project
            topic_match = seed_topic and candidate.dominant_topic == seed_topic
            if not mode_match and not project_match and not topic_match:
                break
            run_end += 1

        run_days = sorted_days[run_start:run_end]
        if len(run_days) >= min_days:
            # Build episode
            mode_counter: Counter[str] = Counter()
            project_counter: Counter[str] = Counter()
            total_active = 0.0
            dominant_mode_count = 0
            dominant_project_count = 0

            for day in run_days:
                total_active += day.active_seconds
                for mode, seconds in day.top_modes:
                    mode_counter[mode] += seconds
                for project, seconds in day.top_projects:
                    project_counter[project] += seconds
                if seed_mode and day.dominant_mode == seed_mode:
                    dominant_mode_count += 1
                if seed_project and day.dominant_project == seed_project:
                    dominant_project_count += 1

            # Determine dominant and trigger
            dominant_mode = seed_mode if dominant_mode_count >= len(run_days) * mode_threshold else None
            dominant_project = seed_project if dominant_project_count >= len(run_days) * mode_threshold else None

            # Topic: count days matching seed topic
            dominant_topic_count = 0
            for day in run_days:
                if seed_topic and day.dominant_topic == seed_topic:
                    dominant_topic_count += 1
            dominant_topic = seed_topic if dominant_topic_count >= len(run_days) * mode_threshold else None

            day_count_with_dominant = max(dominant_mode_count, dominant_project_count, dominant_topic_count)
            trigger = "project_shift" if dominant_project else ("mode_shift" if dominant_mode else ("topic_shift" if dominant_topic else "intensity_change"))

            # Confidence: proportion of days matching dominant * proportion of time
            proportion_days = day_count_with_dominant / len(run_days) if run_days else 0
            if total_active > 0:
                dominant_seconds = 0.0
                if dominant_mode and dominant_mode in mode_counter:
                    dominant_seconds = max(dominant_seconds, mode_counter[dominant_mode])
                if dominant_project and dominant_project in project_counter:
                    dominant_seconds = max(dominant_seconds, project_counter[dominant_project])
                proportion_time = dominant_seconds / total_active
            else:
                proportion_time = 0.0
            confidence = round((proportion_days + proportion_time) / 2.0, 3)

            label = _compose_label(dominant_mode, dominant_project, dominant_topic)
            mode_dist = {k: v for k, v in mode_counter.items()} if mode_counter else {}
            project_dist = {k: v for k, v in project_counter.items()} if project_counter else {}

            episodes.append(TrajectoryEpisode(
                episode_id=_episode_id(run_days[0].date, run_days[-1].date, label),
                label=label,
                start_date=run_days[0].date,
                end_date=run_days[-1].date,
                days=len(run_days),
                active_seconds=round(total_active, 3),
                dominant_mode=dominant_mode,
                dominant_project=dominant_project,
                dominant_topic=dominant_topic,
                mode_distribution=mode_dist,
                project_distribution=project_dist,
                trigger=trigger,
                confidence=confidence,
                day_count_with_dominant=day_count_with_dominant,
            ))

        run_start = run_end

    # Anomaly-cluster trigger: 3+ anomalies within 7 days
    if anomalies:
        anomaly_dates = sorted(set(a.date for a in anomalies))
        i = 0
        while i < len(anomaly_dates):
            cluster_end = i
            for j in range(i + 1, len(anomaly_dates)):
                if (anomaly_dates[j] - anomaly_dates[i]).days <= 7:
                    cluster_end = j
                else:
                    break
            cluster_size = cluster_end - i + 1
            if cluster_size >= 3:
                cluster_start = anomaly_dates[i]
                cluster_end_date = anomaly_dates[cluster_end]
                cluster_days = [d for d in sorted_days if cluster_start <= d.date <= cluster_end_date]
                if len(cluster_days) >= min_days:
                    cluster_anomalies = [a for a in anomalies if cluster_start <= a.date <= cluster_end_date]
                    kinds = sorted(set(a.kind for a in cluster_anomalies))
                    active = sum(d.active_seconds for d in cluster_days)
                    mode_counter: Counter[str] = Counter()
                    project_counter: Counter[str] = Counter()
                    topic_counter: Counter[str] = Counter()
                    for d in cluster_days:
                        if d.dominant_mode:
                            mode_counter[d.dominant_mode] += d.active_seconds
                        if d.dominant_project:
                            project_counter[d.dominant_project] += d.active_seconds
                        if d.dominant_topic:
                            topic_counter[d.dominant_topic] += d.active_seconds
                    dom_mode = mode_counter.most_common(1)[0][0] if mode_counter else None
                    dom_project = project_counter.most_common(1)[0][0] if project_counter else None
                    dom_topic = topic_counter.most_common(1)[0][0] if topic_counter else None
                    label = f"anomaly cluster: {', '.join(kinds)}"
                    ep_id = _episode_id(cluster_start, cluster_end_date, "anomaly_cluster")
                    # Check not overlapping with existing episodes
                    overlaps = any(
                        ep.start_date <= cluster_end_date and ep.end_date >= cluster_start
                        for ep in episodes
                    )
                    if not overlaps:
                        episodes.append(TrajectoryEpisode(
                            episode_id=ep_id,
                            label=label,
                            start_date=cluster_start,
                            end_date=cluster_end_date,
                            days=len(cluster_days),
                            active_seconds=active,
                            dominant_mode=dom_mode,
                            dominant_project=dom_project,
                            dominant_topic=dom_topic,
                            mode_distribution=dict(mode_counter),
                            project_distribution=dict(project_counter),
                            trigger="anomaly_cluster",
                            confidence=min(cluster_size / 5, 0.95),
                            day_count_with_dominant=sum(1 for d in cluster_days if d.dominant_mode == dom_mode),
                        ))
            i = cluster_end + 1

    return episodes
