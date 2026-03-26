"""Anomaly detection across trajectory days.

Identifies statistical deviations and pattern breaks in daily activity:
- rhythm_anomaly: active hours >2σ from rolling baseline
- project_attention_shift: new entry in top-3 projects not in prior window
- recovery_anomaly: recovery/active ratio spike >1.5x
- mode_shift: 3+ consecutive days with different dominant mode
"""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .day import TrajectoryDay


@dataclass(frozen=True)
class TrajectoryAnomaly:
    anomaly_id: str
    date: date
    kind: str  # "mode_shift" | "rhythm_anomaly" | "project_attention_shift" | "recovery_anomaly"
    severity: float  # 0.0–1.0
    description: str
    baseline_value: float
    actual_value: float
    evidence: Optional[dict[str, object]] = None

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


def _anomaly_id(dt: date, kind: str) -> str:
    payload = f"{dt.isoformat()}|{kind}"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def detect_anomalies(
    days: Sequence[TrajectoryDay],
    *,
    rolling_window: int = 14,
    include_processed: bool = True,
) -> list[TrajectoryAnomaly]:
    """Detect anomalies across daily trajectory data.

    Kinds:
    - mode_shift: 3+ consecutive days with a different dominant mode than the rolling baseline
    - rhythm_anomaly: active hours >2σ from rolling mean
    - project_attention_shift: new entry in top-3 projects that wasn't in prior window
    - recovery_anomaly: recovery/active ratio >1.5x from baseline
    """
    if len(days) < rolling_window + 1:
        return []

    sorted_days = sorted(days, key=lambda d: d.date)
    anomalies: list[TrajectoryAnomaly] = []

    for i in range(rolling_window, len(sorted_days)):
        day = sorted_days[i]
        window = sorted_days[max(0, i - rolling_window):i]

        # --- Rhythm anomaly: active hours deviation ---
        window_hours = [d.active_seconds / 3600 for d in window if d.active_seconds > 0]
        if len(window_hours) >= 5:
            mean_hours = statistics.mean(window_hours)
            stdev_hours = statistics.stdev(window_hours) if len(window_hours) > 1 else 0
            day_hours = day.active_seconds / 3600
            if stdev_hours > 0 and abs(day_hours - mean_hours) > 2 * stdev_hours:
                severity = min(abs(day_hours - mean_hours) / (3 * stdev_hours), 1.0)
                direction = "above" if day_hours > mean_hours else "below"
                anomalies.append(TrajectoryAnomaly(
                    anomaly_id=_anomaly_id(day.date, "rhythm_anomaly"),
                    date=day.date,
                    kind="rhythm_anomaly",
                    severity=severity,
                    description=f"Active hours {day_hours:.1f}h is {abs(day_hours - mean_hours) / stdev_hours:.1f}σ {direction} rolling mean {mean_hours:.1f}h",
                    baseline_value=mean_hours,
                    actual_value=day_hours,
                    evidence={"stdev": round(stdev_hours, 2), "direction": direction},
                ))

        # --- Project attention shift: new top-3 entry ---
        window_projects: Counter[str] = Counter()
        for d in window:
            for name, seconds in d.top_projects[:3]:
                window_projects[name] += seconds
        baseline_top3 = {name for name, _ in window_projects.most_common(3)}

        day_top3 = {name for name, _ in day.top_projects[:3]}
        new_entries = day_top3 - baseline_top3
        if new_entries and day.active_seconds > 1800:  # at least 30min active
            for new_proj in new_entries:
                proj_hours = next((s / 3600 for n, s in day.top_projects if n == new_proj), 0)
                if proj_hours > 0.5:  # at least 30min on the new project
                    anomalies.append(TrajectoryAnomaly(
                        anomaly_id=_anomaly_id(day.date, f"project_attention_shift_{new_proj}"),
                        date=day.date,
                        kind="project_attention_shift",
                        severity=min(proj_hours / 4.0, 1.0),
                        description=f"{new_proj} entered top-3 ({proj_hours:.1f}h), absent from prior {rolling_window}d baseline",
                        baseline_value=0.0,
                        actual_value=proj_hours,
                        evidence={"project": new_proj, "baseline_top3": sorted(baseline_top3)},
                    ))

        # --- Recovery anomaly: ratio spike ---
        window_ratios = []
        for d in window:
            if d.active_seconds > 1800:
                window_ratios.append(d.recovery_seconds / max(d.active_seconds, 1))
        if window_ratios and day.active_seconds > 1800:
            mean_ratio = statistics.mean(window_ratios)
            day_ratio = day.recovery_seconds / max(day.active_seconds, 1)
            if mean_ratio > 0 and day_ratio > mean_ratio * 1.5:
                anomalies.append(TrajectoryAnomaly(
                    anomaly_id=_anomaly_id(day.date, "recovery_anomaly"),
                    date=day.date,
                    kind="recovery_anomaly",
                    severity=min((day_ratio / mean_ratio - 1) / 2, 1.0),
                    description=f"Recovery ratio {day_ratio:.2f} vs baseline {mean_ratio:.2f} ({day_ratio / mean_ratio:.1f}x)",
                    baseline_value=mean_ratio,
                    actual_value=day_ratio,
                ))

    # --- Mode shift: 3+ consecutive days with different dominant mode ---
    if len(sorted_days) >= rolling_window + 3:
        for i in range(rolling_window, len(sorted_days) - 2):
            window = sorted_days[max(0, i - rolling_window):i]
            window_modes: Counter[str] = Counter()
            for d in window:
                if d.dominant_mode:
                    window_modes[d.dominant_mode] += d.active_seconds
            if not window_modes:
                continue
            baseline_mode = window_modes.most_common(1)[0][0]

            run = sorted_days[i:i + 3]
            if all(d.dominant_mode and d.dominant_mode != baseline_mode for d in run):
                new_mode = run[0].dominant_mode
                # Only flag once per run start
                if i == rolling_window or sorted_days[i - 1].dominant_mode == baseline_mode:
                    run_hours = sum(d.active_seconds / 3600 for d in run)
                    anomalies.append(TrajectoryAnomaly(
                        anomaly_id=_anomaly_id(run[0].date, f"mode_shift_{new_mode}"),
                        date=run[0].date,
                        kind="mode_shift",
                        severity=min(run_hours / 15.0, 1.0),
                        description=f"3+ day mode shift: {baseline_mode} → {new_mode} starting {run[0].date}",
                        baseline_value=0.0,
                        actual_value=run_hours,
                        evidence={"from_mode": baseline_mode, "to_mode": new_mode, "run_days": 3},
                    ))

    # --- Processed-source anomalies (require live data access) ---
    if include_processed:
        try:
            anomalies.extend(_detect_deep_work_drought(sorted_days, rolling_window))
        except Exception:
            pass

        try:
            anomalies.extend(_detect_fragmentation_spike(sorted_days, rolling_window))
        except Exception:
            pass

    anomalies.sort(key=lambda a: (a.date, a.kind))
    return anomalies


def _detect_deep_work_drought(
    sorted_days: list[TrajectoryDay],
    rolling_window: int,
) -> list[TrajectoryAnomaly]:
    """3+ consecutive days without any deep work block (>30min).

    Uses the deep_work processed module to check each day for blocks.
    """
    from datetime import datetime, timedelta

    anomalies: list[TrajectoryAnomaly] = []

    try:
        from ..sources.processed.deep_work import iter_deep_work
    except ImportError:
        return anomalies

    if len(sorted_days) < 3:
        return anomalies

    # Check each day for deep work presence
    days_with_deep_work: set[date] = set()
    first_date = sorted_days[0].date
    last_date = sorted_days[-1].date
    total_blocks = 0
    try:
        dt_start = datetime(first_date.year, first_date.month, first_date.day)
        dt_end = datetime(last_date.year, last_date.month, last_date.day) + timedelta(days=1)
        for block in iter_deep_work(start=dt_start, end=dt_end):
            total_blocks += 1
            if block.duration_minutes >= 30:
                days_with_deep_work.add(block.start.date())
    except Exception:
        return anomalies

    # If deep work was found on fewer than 20% of active days, data coverage
    # is too sparse to reliably detect droughts.
    active_days = sum(1 for d in sorted_days if d.active_seconds >= 1800)
    if active_days == 0 or len(days_with_deep_work) / active_days < 0.2:
        return anomalies

    # Find runs of 3+ consecutive active days without deep work
    consecutive_drought = 0
    drought_start: date | None = None

    for day in sorted_days:
        if day.active_seconds < 1800:  # skip inactive days
            consecutive_drought = 0
            drought_start = None
            continue

        if day.date not in days_with_deep_work:
            if consecutive_drought == 0:
                drought_start = day.date
            consecutive_drought += 1

            if consecutive_drought == 3 and drought_start is not None:
                anomalies.append(TrajectoryAnomaly(
                    anomaly_id=_anomaly_id(drought_start, "deep_work_drought"),
                    date=drought_start,
                    kind="deep_work_drought",
                    severity=min(consecutive_drought / 7.0, 1.0),
                    description=f"3+ consecutive active days without deep work (>30min) starting {drought_start}",
                    baseline_value=1.0,  # expected: at least 1 block/day
                    actual_value=0.0,
                    evidence={"drought_days": consecutive_drought},
                ))
        else:
            consecutive_drought = 0
            drought_start = None

    return anomalies


def _detect_fragmentation_spike(
    sorted_days: list[TrajectoryDay],
    rolling_window: int,
) -> list[TrajectoryAnomaly]:
    """Context switch fragmentation_score >2 sigma from 14-day baseline."""
    anomalies: list[TrajectoryAnomaly] = []

    try:
        from ..sources.processed.context_switches import iter_context_switch_metrics
    except ImportError:
        return anomalies

    if len(sorted_days) < rolling_window + 1:
        return anomalies

    first_date = sorted_days[0].date
    last_date = sorted_days[-1].date
    try:
        metrics = list(iter_context_switch_metrics(start=first_date, end=last_date))
    except Exception:
        return anomalies

    if len(metrics) < rolling_window + 1:
        return anomalies

    for i in range(rolling_window, len(metrics)):
        window_scores = [m.fragmentation_score for m in metrics[max(0, i - rolling_window):i]]
        current = metrics[i]

        if len(window_scores) < 5:
            continue

        mean_score = statistics.mean(window_scores)
        stdev_score = statistics.stdev(window_scores) if len(window_scores) > 1 else 0
        if stdev_score <= 0:
            continue

        z_score = (current.fragmentation_score - mean_score) / stdev_score
        if z_score > 2.0:
            anomalies.append(TrajectoryAnomaly(
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
            ))

    return anomalies
