"""Health/Sleep → Productivity evidence bridge.

Converts sleep_productivity() and daily_health_summary() data into
evidence graph nodes: sleep_quality, health_metric, sleep_productivity_link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class SleepQualityNode:
    id: str
    date: date
    sleep_hours: float
    sleep_score: int | None
    sleep_quality: str | None
    deep_pct: float | None
    rem_pct: float | None
    stage_transitions: int | None
    first_rem_min: float | None
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class HealthMetricNode:
    id: str
    date: date
    metric: str  # steps, stress, hrv, heart_rate, spo2, respiratory, skin_temp, vitality
    value: float
    unit: str
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SleepProductivityLink:
    id: str
    sleep_date: date
    workday_date: date
    sleep_hours: float
    workday_active_hours: float
    workday_deep_work_min: float
    productivity_vs_baseline: str | None
    summary: str
    payload: dict[str, Any]


def build_sleep_evidence(
    *,
    start: date,
    end: date,
) -> tuple[SleepQualityNode, ...]:
    """Build sleep quality evidence nodes."""
    from ..sources.sleep import sleep_architecture

    nodes: list[SleepQualityNode] = []
    for arch in sleep_architecture(start=start, end=end):
        nodes.append(SleepQualityNode(
            id=f"sleep:{arch.date.isoformat()}",
            date=arch.date,
            sleep_hours=round(arch.total_min / 60.0, 1),
            sleep_score=None,
            sleep_quality=None,
            deep_pct=round(arch.deep_pct, 1) if arch.deep_pct else None,
            rem_pct=round(arch.rem_pct, 1) if arch.rem_pct else None,
            stage_transitions=arch.stage_transitions,
            first_rem_min=round(arch.first_rem_min, 1) if arch.first_rem_min else None,
            summary=(
                f"Sleep: {arch.total_min / 60:.1f}h, "
                f"deep={arch.deep_pct:.0f}%, rem={arch.rem_pct:.0f}%, "
                f"{arch.stage_transitions} transitions"
            ),
            payload={
                "total_min": arch.total_min,
                "awake_min": arch.awake_min,
                "light_min": arch.light_min,
                "deep_min": arch.deep_min,
                "rem_min": arch.rem_min,
                "deep_pct": arch.deep_pct,
                "rem_pct": arch.rem_pct,
                "stage_transitions": arch.stage_transitions,
                "first_rem_min": arch.first_rem_min,
            },
        ))
    return tuple(nodes)


def build_health_evidence(
    *,
    start: date,
    end: date,
) -> tuple[HealthMetricNode, ...]:
    """Build health metric evidence nodes."""
    from ..sources.health import daily_health_summary

    nodes: list[HealthMetricNode] = []
    for summary in daily_health_summary(start=start, end=end):
        d = summary.date

        if summary.steps and summary.steps > 0:
            nodes.append(HealthMetricNode(
                id=f"health:steps:{d.isoformat()}",
                date=d, metric="steps", value=float(summary.steps), unit="steps",
                summary=f"{summary.steps:,} steps",
                payload={"value": summary.steps},
            ))
        if summary.stress_avg and summary.stress_avg > 0:
            nodes.append(HealthMetricNode(
                id=f"health:stress:{d.isoformat()}",
                date=d, metric="stress", value=round(summary.stress_avg, 1), unit="score",
                summary=f"stress avg={summary.stress_avg:.1f} ({summary.stress_count} measurements)",
                payload={"avg": summary.stress_avg, "count": summary.stress_count},
            ))
        if summary.heart_rate_resting and summary.heart_rate_resting > 0:
            nodes.append(HealthMetricNode(
                id=f"health:hr:{d.isoformat()}",
                date=d, metric="heart_rate", value=float(summary.heart_rate_resting), unit="bpm",
                summary=f"resting HR={summary.heart_rate_resting} bpm",
                payload={"resting": summary.heart_rate_resting, "avg": summary.heart_rate_avg},
            ))
        if summary.hrv_rmssd_avg and summary.hrv_rmssd_avg > 0:
            nodes.append(HealthMetricNode(
                id=f"health:hrv:{d.isoformat()}",
                date=d, metric="hrv", value=round(summary.hrv_rmssd_avg, 1), unit="ms",
                summary=f"HRV rmssd={summary.hrv_rmssd_avg:.1f}ms ({summary.hrv_count} measurements)",
                payload={"rmssd_avg": summary.hrv_rmssd_avg, "count": summary.hrv_count},
            ))
        if summary.spo2_avg and summary.spo2_avg > 0:
            nodes.append(HealthMetricNode(
                id=f"health:spo2:{d.isoformat()}",
                date=d, metric="spo2", value=round(summary.spo2_avg, 1), unit="%",
                summary=f"SpO2={summary.spo2_avg:.1f}%",
                payload={"avg": summary.spo2_avg, "count": summary.spo2_count},
            ))
        if summary.vitality_score and summary.vitality_score > 0:
            nodes.append(HealthMetricNode(
                id=f"health:vitality:{d.isoformat()}",
                date=d, metric="vitality", value=round(summary.vitality_score, 1), unit="score",
                summary=f"vitality={summary.vitality_score:.1f}",
                payload={"score": summary.vitality_score, "calories": summary.calories},
            ))

    return tuple(nodes)


def build_sleep_productivity_links(
    *,
    start: date,
    end: date,
) -> tuple[SleepProductivityLink, ...]:
    """Build sleep-to-productivity correlation links."""
    from ..sources.sleep import sleep_productivity

    links: list[SleepProductivityLink] = []
    for sp in sleep_productivity(start=start, end=end):
        links.append(SleepProductivityLink(
            id=f"sleep-prod:{sp.sleep_date.isoformat()}",
            sleep_date=sp.sleep_date,
            workday_date=sp.sleep_date,  # next-day
            sleep_hours=round(sp.sleep_hours, 1),
            workday_active_hours=round(sp.workday_active_hours, 1),
            workday_deep_work_min=round(sp.workday_deep_work_min, 1),
            productivity_vs_baseline=sp.productivity_vs_baseline,
            summary=(
                f"Sleep {sp.sleep_hours:.1f}h (score={sp.sleep_score}) → "
                f"next-day active={sp.workday_active_hours:.1f}h, "
                f"deep work={sp.workday_deep_work_min:.0f}min "
                f"({sp.productivity_vs_baseline or 'vs baseline'})"
            ),
            payload={
                "sleep_hours": sp.sleep_hours,
                "sleep_score": sp.sleep_score,
                "sleep_quality": sp.sleep_quality,
                "workday_active_hours": sp.workday_active_hours,
                "workday_deep_work_min": sp.workday_deep_work_min,
                "productivity_vs_baseline": sp.productivity_vs_baseline,
            },
        ))
    return tuple(links)


__all__ = [
    "HealthMetricNode",
    "SleepProductivityLink",
    "SleepQualityNode",
    "build_health_evidence",
    "build_sleep_evidence",
    "build_sleep_productivity_links",
]
