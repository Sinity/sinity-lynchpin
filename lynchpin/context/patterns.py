"""Context-owned pattern detection over evidence-derived day summaries."""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from .summary_models import DaySummary, EpisodeSummary


@dataclass(frozen=True)
class ContextAnomaly:
    anomaly_id: str
    date: date
    kind: str
    severity: float
    description: str
    baseline_value: float
    actual_value: float
    evidence: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "anomaly_id": self.anomaly_id,
            "date": self.date.isoformat(),
            "kind": self.kind,
            "severity": round(self.severity, 3),
            "description": self.description,
            "baseline_value": round(self.baseline_value, 3),
            "actual_value": round(self.actual_value, 3),
            "evidence": self.evidence or {},
        }


def detect_anomalies(
    days: Sequence[DaySummary],
    *,
    rolling_window: int = 14,
    include_processed: bool = True,
) -> list[ContextAnomaly]:
    if len(days) < rolling_window + 1:
        return []

    sorted_days = sorted(days, key=lambda item: item.date)
    anomalies: list[ContextAnomaly] = []

    for index in range(rolling_window, len(sorted_days)):
        day = sorted_days[index]
        window = sorted_days[max(0, index - rolling_window):index]

        window_hours = [item.active_seconds / 3600.0 for item in window if item.active_seconds > 0]
        if len(window_hours) >= 5:
            mean_hours = statistics.mean(window_hours)
            stdev_hours = statistics.stdev(window_hours) if len(window_hours) > 1 else 0.0
            day_hours = day.active_seconds / 3600.0
            if stdev_hours > 0 and abs(day_hours - mean_hours) > 2 * stdev_hours:
                direction = "above" if day_hours > mean_hours else "below"
                anomalies.append(
                    ContextAnomaly(
                        anomaly_id=_anomaly_id(day.date, "rhythm_anomaly"),
                        date=day.date,
                        kind="rhythm_anomaly",
                        severity=min(abs(day_hours - mean_hours) / (3 * stdev_hours), 1.0),
                        description=(
                            f"Active hours {day_hours:.1f}h is "
                            f"{abs(day_hours - mean_hours) / stdev_hours:.1f}σ {direction} "
                            f"rolling mean {mean_hours:.1f}h"
                        ),
                        baseline_value=mean_hours,
                        actual_value=day_hours,
                        evidence={"stdev": round(stdev_hours, 2), "direction": direction},
                    )
                )

        window_projects: Counter[str] = Counter()
        for item in window:
            for name, seconds in item.top_projects[:3]:
                window_projects[name] += seconds
        baseline_top3 = {name for name, _ in window_projects.most_common(3)}
        day_top3 = {name for name, _ in day.top_projects[:3]}
        new_entries = day_top3 - baseline_top3
        if new_entries and day.active_seconds > 1800:
            for new_project in new_entries:
                project_hours = next((seconds / 3600.0 for name, seconds in day.top_projects if name == new_project), 0.0)
                if project_hours <= 0.5:
                    continue
                anomalies.append(
                    ContextAnomaly(
                        anomaly_id=_anomaly_id(day.date, f"project_attention_shift_{new_project}"),
                        date=day.date,
                        kind="project_attention_shift",
                        severity=min(project_hours / 4.0, 1.0),
                        description=(
                            f"{new_project} entered top-3 ({project_hours:.1f}h), absent from prior "
                            f"{rolling_window}d baseline"
                        ),
                        baseline_value=0.0,
                        actual_value=project_hours,
                        evidence={"project": new_project, "baseline_top3": sorted(baseline_top3)},
                    )
                )

        window_ratios = [
            item.recovery_seconds / max(item.active_seconds, 1)
            for item in window
            if item.active_seconds > 1800
        ]
        if window_ratios and day.active_seconds > 1800:
            mean_ratio = statistics.mean(window_ratios)
            day_ratio = day.recovery_seconds / max(day.active_seconds, 1)
            if mean_ratio > 0 and day_ratio > mean_ratio * 1.5:
                anomalies.append(
                    ContextAnomaly(
                        anomaly_id=_anomaly_id(day.date, "recovery_anomaly"),
                        date=day.date,
                        kind="recovery_anomaly",
                        severity=min((day_ratio / mean_ratio - 1) / 2, 1.0),
                        description=(
                            f"Recovery ratio {day_ratio:.2f} vs baseline {mean_ratio:.2f} "
                            f"({day_ratio / mean_ratio:.1f}x)"
                        ),
                        baseline_value=mean_ratio,
                        actual_value=day_ratio,
                    )
                )

    if len(sorted_days) >= rolling_window + 3:
        for index in range(rolling_window, len(sorted_days) - 2):
            window = sorted_days[max(0, index - rolling_window):index]
            window_modes: Counter[str] = Counter()
            for item in window:
                if item.dominant_mode:
                    window_modes[item.dominant_mode] += item.active_seconds
            if not window_modes:
                continue
            baseline_mode = window_modes.most_common(1)[0][0]
            run = sorted_days[index:index + 3]
            if all(item.dominant_mode and item.dominant_mode != baseline_mode for item in run):
                new_mode = run[0].dominant_mode
                if index == rolling_window or sorted_days[index - 1].dominant_mode == baseline_mode:
                    run_hours = sum(item.active_seconds / 3600.0 for item in run)
                    anomalies.append(
                        ContextAnomaly(
                            anomaly_id=_anomaly_id(run[0].date, f"mode_shift_{new_mode}"),
                            date=run[0].date,
                            kind="mode_shift",
                            severity=min(run_hours / 15.0, 1.0),
                            description=f"3+ day mode shift: {baseline_mode} → {new_mode} starting {run[0].date}",
                            baseline_value=0.0,
                            actual_value=run_hours,
                            evidence={"from_mode": baseline_mode, "to_mode": new_mode, "run_days": 3},
                        )
                    )

    if include_processed:
        try:
            anomalies.extend(_detect_deep_work_drought(sorted_days, rolling_window))
        except Exception:
            pass
        try:
            anomalies.extend(_detect_fragmentation_spike(sorted_days, rolling_window))
        except Exception:
            pass

    anomalies.sort(key=lambda item: (item.date, item.kind))
    return anomalies


def detect_episodes(
    days: Sequence[DaySummary],
    *,
    min_days: int = 2,
    mode_threshold: float = 0.4,
    anomalies: Sequence[ContextAnomaly] | None = None,
) -> list[EpisodeSummary]:
    if len(days) < min_days:
        return []

    sorted_days = sorted(days, key=lambda item: item.date)
    episodes: list[EpisodeSummary] = []
    run_start = 0
    while run_start < len(sorted_days):
        seed_day = sorted_days[run_start]
        seed_mode = seed_day.dominant_mode
        seed_project = seed_day.dominant_project
        seed_topic = seed_day.dominant_topic
        if not seed_mode and not seed_project and not seed_topic:
            run_start += 1
            continue

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
            mode_counter: Counter[str] = Counter()
            project_counter: Counter[str] = Counter()
            total_active = 0.0
            dominant_mode_count = 0
            dominant_project_count = 0
            dominant_topic_count = 0

            for item in run_days:
                total_active += item.active_seconds
                for mode, seconds in item.top_modes:
                    mode_counter[mode] += seconds
                for project, seconds in item.top_projects:
                    project_counter[project] += seconds
                if seed_mode and item.dominant_mode == seed_mode:
                    dominant_mode_count += 1
                if seed_project and item.dominant_project == seed_project:
                    dominant_project_count += 1
                if seed_topic and item.dominant_topic == seed_topic:
                    dominant_topic_count += 1

            dominant_mode = seed_mode if dominant_mode_count >= len(run_days) * mode_threshold else None
            dominant_project = seed_project if dominant_project_count >= len(run_days) * mode_threshold else None
            dominant_topic = seed_topic if dominant_topic_count >= len(run_days) * mode_threshold else None
            dominant_days = max(dominant_mode_count, dominant_project_count, dominant_topic_count)
            trigger = (
                "project_shift"
                if dominant_project
                else ("mode_shift" if dominant_mode else ("topic_shift" if dominant_topic else "intensity_change"))
            )
            proportion_days = dominant_days / len(run_days) if run_days else 0.0
            dominant_seconds = 0.0
            if total_active > 0:
                if dominant_mode and dominant_mode in mode_counter:
                    dominant_seconds = max(dominant_seconds, mode_counter[dominant_mode])
                if dominant_project and dominant_project in project_counter:
                    dominant_seconds = max(dominant_seconds, project_counter[dominant_project])
            proportion_time = dominant_seconds / total_active if total_active else 0.0
            episodes.append(
                EpisodeSummary(
                    episode_id=_episode_id(run_days[0].date, run_days[-1].date, _compose_label(dominant_mode, dominant_project, dominant_topic)),
                    label=_compose_label(dominant_mode, dominant_project, dominant_topic),
                    start_date=run_days[0].date,
                    end_date=run_days[-1].date,
                    days=len(run_days),
                    active_seconds=round(total_active, 3),
                    dominant_mode=dominant_mode,
                    dominant_project=dominant_project,
                    dominant_topic=dominant_topic,
                    trigger=trigger,
                    confidence=round((proportion_days + proportion_time) / 2.0, 3),
                )
            )

        run_start = run_end

    if anomalies:
        anomaly_dates = sorted({item.date for item in anomalies})
        index = 0
        while index < len(anomaly_dates):
            cluster_end = index
            for inner in range(index + 1, len(anomaly_dates)):
                if (anomaly_dates[inner] - anomaly_dates[index]).days <= 7:
                    cluster_end = inner
                else:
                    break
            cluster_size = cluster_end - index + 1
            if cluster_size >= 3:
                cluster_start = anomaly_dates[index]
                cluster_end_date = anomaly_dates[cluster_end]
                cluster_days = [item for item in sorted_days if cluster_start <= item.date <= cluster_end_date]
                if len(cluster_days) >= min_days:
                    mode_counter: Counter[str] = Counter()
                    project_counter: Counter[str] = Counter()
                    topic_counter: Counter[str] = Counter()
                    for item in cluster_days:
                        if item.dominant_mode:
                            mode_counter[item.dominant_mode] += item.active_seconds
                        if item.dominant_project:
                            project_counter[item.dominant_project] += item.active_seconds
                        if item.dominant_topic:
                            topic_counter[item.dominant_topic] += item.active_seconds
                    dom_mode = _dominant_label(mode_counter)
                    dom_project = _dominant_label(project_counter)
                    dom_topic = _dominant_label(topic_counter)
                    overlaps = any(
                        episode.start_date <= cluster_end_date and episode.end_date >= cluster_start
                        for episode in episodes
                    )
                    if not overlaps:
                        episodes.append(
                            EpisodeSummary(
                                episode_id=_episode_id(cluster_start, cluster_end_date, "anomaly_cluster"),
                                label="anomaly cluster",
                                start_date=cluster_start,
                                end_date=cluster_end_date,
                                days=len(cluster_days),
                                active_seconds=sum(item.active_seconds for item in cluster_days),
                                dominant_mode=dom_mode,
                                dominant_project=dom_project,
                                dominant_topic=dom_topic,
                                trigger="anomaly_cluster",
                                confidence=min(cluster_size / 5, 0.95),
                            )
                        )
            index = cluster_end + 1

    episodes.sort(key=lambda item: (item.start_date, item.end_date, item.episode_id))
    return episodes


def build_recent_focus_loops(loop_rows: Sequence[dict[str, Any]], *, limit: int = 15) -> list[dict[str, object]]:
    ordered = sorted(loop_rows, key=lambda row: (_coerce_datetime(row.get("start")) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    packets: list[dict[str, object]] = []
    for row in ordered[:limit]:
        start = _coerce_datetime(row.get("start"))
        end = _coerce_datetime(row.get("end_time") or row.get("end"))
        if start is None or end is None:
            continue
        payload = {
            "loop_id": _loop_id(start, end, row),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration_minutes": round(float(row.get("duration_minutes") or 0.0), 2),
            "span_count": int(row.get("span_count") or 0),
            "switch_count": int(row.get("switch_count") or 0),
            "cycle_count": int(row.get("cycle_count") or 0),
            "dominant_mode": row.get("dominant_mode"),
            "dominant_project": row.get("dominant_project"),
            "contexts": [
                f"{row.get('context_a_app') or 'unknown'} :: {row.get('context_a_title') or '(untitled)'}",
                f"{row.get('context_b_app') or 'unknown'} :: {row.get('context_b_title') or '(untitled)'}",
            ],
        }
        packets.append(payload)
    return packets


def _detect_deep_work_drought(sorted_days: list[DaySummary], rolling_window: int) -> list[ContextAnomaly]:
    from datetime import datetime as _datetime, timedelta as _timedelta
    from ..sources.processed.deep_work import iter_deep_work

    anomalies: list[ContextAnomaly] = []
    if len(sorted_days) < 3:
        return anomalies

    first_date = sorted_days[0].date
    last_date = sorted_days[-1].date
    dt_start = _datetime(first_date.year, first_date.month, first_date.day)
    dt_end = _datetime(last_date.year, last_date.month, last_date.day) + _timedelta(days=1)
    days_with_deep_work: set[date] = set()
    for block in iter_deep_work(start=dt_start, end=dt_end):
        if block.duration_minutes >= 30:
            days_with_deep_work.add(block.start.date())

    active_days = sum(1 for item in sorted_days if item.active_seconds >= 1800)
    if active_days == 0 or len(days_with_deep_work) / active_days < 0.2:
        return anomalies

    consecutive_drought = 0
    drought_start: date | None = None
    for item in sorted_days:
        if item.active_seconds < 1800:
            consecutive_drought = 0
            drought_start = None
            continue
        if item.date not in days_with_deep_work:
            if consecutive_drought == 0:
                drought_start = item.date
            consecutive_drought += 1
            if consecutive_drought == 3 and drought_start is not None:
                anomalies.append(
                    ContextAnomaly(
                        anomaly_id=_anomaly_id(drought_start, "deep_work_drought"),
                        date=drought_start,
                        kind="deep_work_drought",
                        severity=min(consecutive_drought / 7.0, 1.0),
                        description=f"3+ consecutive active days without deep work (>30min) starting {drought_start}",
                        baseline_value=1.0,
                        actual_value=0.0,
                        evidence={"drought_days": consecutive_drought},
                    )
                )
        else:
            consecutive_drought = 0
            drought_start = None
    return anomalies


def _detect_fragmentation_spike(sorted_days: list[DaySummary], rolling_window: int) -> list[ContextAnomaly]:
    from ..sources.processed.context_switches import iter_context_switch_metrics

    anomalies: list[ContextAnomaly] = []
    if len(sorted_days) < rolling_window + 1:
        return anomalies

    metrics = list(iter_context_switch_metrics(start=sorted_days[0].date, end=sorted_days[-1].date))
    if len(metrics) < rolling_window + 1:
        return anomalies

    for index in range(rolling_window, len(metrics)):
        window_scores = [item.fragmentation_score for item in metrics[max(0, index - rolling_window):index]]
        current = metrics[index]
        if len(window_scores) < 5:
            continue
        mean_score = statistics.mean(window_scores)
        stdev_score = statistics.stdev(window_scores) if len(window_scores) > 1 else 0.0
        if stdev_score <= 0:
            continue
        z_score = (current.fragmentation_score - mean_score) / stdev_score
        if z_score > 2.0:
            anomalies.append(
                ContextAnomaly(
                    anomaly_id=_anomaly_id(current.date, "fragmentation_spike"),
                    date=current.date,
                    kind="fragmentation_spike",
                    severity=min(z_score / 4.0, 1.0),
                    description=(
                        f"Fragmentation score {current.fragmentation_score:.2f} is "
                        f"{z_score:.1f}σ above {rolling_window}d baseline {mean_score:.2f}"
                    ),
                    baseline_value=mean_score,
                    actual_value=current.fragmentation_score,
                    evidence={
                        "z_score": round(z_score, 2),
                        "stdev": round(stdev_score, 3),
                        "total_switches": current.total_switches,
                    },
                )
            )
    return anomalies


def _anomaly_id(target_date: date, kind: str) -> str:
    payload = f"{target_date.isoformat()}|{kind}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _episode_id(start: date, end: date, label: str) -> str:
    payload = f"{start.isoformat()}|{end.isoformat()}|{label}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _compose_label(mode: Optional[str], project: Optional[str], topic: Optional[str]) -> str:
    parts: list[str] = []
    if project:
        parts.append(project)
    if topic and topic != (project or "").lower():
        parts.append(topic)
    if mode:
        parts.append(mode)
    return " ".join(parts) if parts else "mixed activity"


def _dominant_label(counter: Counter[str]) -> str | None:
    return counter.most_common(1)[0][0] if counter else None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _loop_id(start: datetime, end: datetime, row: dict[str, Any]) -> str:
    payload = "|".join(
        [
            start.isoformat(),
            end.isoformat(),
            str(row.get("context_a_app") or ""),
            str(row.get("context_a_title") or ""),
            str(row.get("context_b_app") or ""),
            str(row.get("context_b_title") or ""),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
