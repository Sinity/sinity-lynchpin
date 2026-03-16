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

    anomalies.sort(key=lambda a: (a.date, a.kind))
    return anomalies
