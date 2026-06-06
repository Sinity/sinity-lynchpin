"""Health/Sleep → Productivity evidence bridge.

Converts sleep_productivity() and daily_health_summary() data into
evidence graph nodes: sleep_quality, health_metric, sleep_productivity_link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
    ensure: bool = True,
) -> tuple[SleepQualityNode, ...]:
    """Build sleep quality evidence nodes."""
    nodes: list[SleepQualityNode] = []
    for day, metrics, dimensions in _personal_signal_metrics(
        source="sleep",
        start=start,
        end=end + timedelta(days=1),
        ensure=ensure,
    ):
        total_min = metrics.get("sleep_arch_total_minutes", metrics.get("sleep_minutes"))
        if total_min is None:
            continue
        deep_pct = metrics.get("sleep_deep_pct")
        rem_pct = metrics.get("sleep_rem_pct")
        stage_transitions = metrics.get("sleep_stage_transitions")
        first_rem_min = metrics.get("sleep_first_rem_minutes")
        score = metrics.get("sleep_score")
        quality = dimensions.get("sleep_minutes", {}).get("quality")
        sleep_id = dimensions.get("sleep_arch_total_minutes", {}).get("sleep_id")
        summary_bits = [f"Sleep: {total_min / 60:.1f}h"]
        if deep_pct is not None and rem_pct is not None and stage_transitions is not None:
            summary_bits.append(
                f"deep={deep_pct:.0f}%, rem={rem_pct:.0f}%, {stage_transitions:.0f} transitions"
            )
        elif score is not None:
            summary_bits.append(f"score={score:.0f}")
        nodes.append(SleepQualityNode(
            id=f"sleep:{day.isoformat()}",
            date=day,
            sleep_hours=round(total_min / 60.0, 1),
            sleep_score=int(score) if score is not None else None,
            sleep_quality=str(quality) if quality is not None else None,
            deep_pct=round(deep_pct, 1) if deep_pct is not None else None,
            rem_pct=round(rem_pct, 1) if rem_pct is not None else None,
            stage_transitions=int(stage_transitions) if stage_transitions is not None else None,
            first_rem_min=round(first_rem_min, 1) if first_rem_min is not None else None,
            summary=", ".join(summary_bits),
            payload={
                "total_min": total_min,
                "sleep_score": score,
                "sleep_quality": quality,
                "sleep_id": sleep_id,
                "awake_min": metrics.get("sleep_awake_minutes"),
                "light_min": metrics.get("sleep_light_minutes"),
                "deep_min": metrics.get("sleep_deep_minutes"),
                "rem_min": metrics.get("sleep_rem_minutes"),
                "deep_pct": deep_pct,
                "rem_pct": rem_pct,
                "stage_transitions": int(stage_transitions) if stage_transitions is not None else None,
                "first_rem_min": first_rem_min,
            },
        ))
    return tuple(nodes)


def build_health_evidence(
    *,
    start: date,
    end: date,
    ensure: bool = True,
) -> tuple[HealthMetricNode, ...]:
    """Build health metric evidence nodes."""
    nodes: list[HealthMetricNode] = []
    for d, metrics, dimensions in _personal_signal_metrics(
        source="health",
        start=start,
        end=end + timedelta(days=1),
        ensure=ensure,
    ):
        steps = metrics.get("steps")
        if steps is not None and steps > 0:
            nodes.append(HealthMetricNode(
                id=f"health:steps:{d.isoformat()}",
                date=d, metric="steps", value=float(steps), unit="steps",
                summary=f"{int(steps):,} steps",
                payload={"value": int(steps)},
            ))
        stress = metrics.get("stress_avg")
        if stress is not None and stress > 0:
            count = _int_dimension(dimensions, "stress_avg", "count")
            nodes.append(HealthMetricNode(
                id=f"health:stress:{d.isoformat()}",
                date=d, metric="stress", value=round(stress, 1), unit="score",
                summary=f"stress avg={stress:.1f} ({count} measurements)",
                payload={"avg": stress, "count": count},
            ))
        resting_hr = metrics.get("resting_heart_rate")
        if resting_hr is not None and resting_hr > 0:
            nodes.append(HealthMetricNode(
                id=f"health:hr:{d.isoformat()}",
                date=d, metric="heart_rate", value=float(resting_hr), unit="bpm",
                summary=f"resting HR={resting_hr:g} bpm",
                payload={"resting": resting_hr, "avg": metrics.get("avg_heart_rate")},
            ))
        hrv = metrics.get("hrv_rmssd")
        if hrv is not None and hrv > 0:
            count = _int_dimension(dimensions, "hrv_rmssd", "count")
            nodes.append(HealthMetricNode(
                id=f"health:hrv:{d.isoformat()}",
                date=d, metric="hrv", value=round(hrv, 1), unit="ms",
                summary=f"HRV rmssd={hrv:.1f}ms ({count} measurements)",
                payload={"rmssd_avg": hrv, "count": count},
            ))
        spo2 = metrics.get("spo2_avg")
        if spo2 is not None and spo2 > 0:
            count = _int_dimension(dimensions, "spo2_avg", "count")
            nodes.append(HealthMetricNode(
                id=f"health:spo2:{d.isoformat()}",
                date=d, metric="spo2", value=round(spo2, 1), unit="%",
                summary=f"SpO2={spo2:.1f}%",
                payload={"avg": spo2, "count": count},
            ))
        vitality = metrics.get("vitality_score")
        if vitality is not None and vitality > 0:
            nodes.append(HealthMetricNode(
                id=f"health:vitality:{d.isoformat()}",
                date=d, metric="vitality", value=round(vitality, 1), unit="score",
                summary=f"vitality={vitality:.1f}",
                payload={"score": vitality, "calories": dimensions.get("vitality_score", {}).get("calories")},
            ))

    return tuple(nodes)


def _personal_signal_metrics(
    *,
    source: str,
    start: date,
    end: date,
    ensure: bool,
) -> tuple[tuple[date, dict[str, float], dict[str, dict[str, Any]]], ...]:
    from ..sources.personal_signals import iter_personal_daily_signals

    metrics: dict[date, dict[str, float]] = {}
    dimensions: dict[date, dict[str, dict[str, Any]]] = {}
    for row in iter_personal_daily_signals(start=start, end=end, ensure=ensure):
        if row.source != source:
            continue
        metrics.setdefault(row.date, {})[row.metric] = row.value
        dimensions.setdefault(row.date, {})[row.metric] = row.dimensions
    return tuple(
        (day, metrics[day], dimensions.get(day, {}))
        for day in sorted(metrics)
    )


def _int_dimension(dimensions: dict[str, dict[str, Any]], metric: str, key: str) -> int:
    value = dimensions.get(metric, {}).get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
            workday_date=sp.sleep_date + timedelta(days=1),
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


def sleep_productivity_link_from_row(row: Any) -> SleepProductivityLink:
    return SleepProductivityLink(
        id=f"sleep-prod:{row.sleep_date.isoformat()}",
        sleep_date=row.sleep_date,
        workday_date=row.sleep_date + timedelta(days=1),
        sleep_hours=round(row.sleep_hours, 1),
        workday_active_hours=round(row.workday_active_hours, 1),
        workday_deep_work_min=round(row.workday_deep_work_min, 1),
        productivity_vs_baseline=str(row.productivity_vs_baseline),
        summary=(
            f"Sleep {row.sleep_hours:.1f}h (score={row.sleep_score}) -> "
            f"next-day active={row.workday_active_hours:.1f}h, "
            f"deep work={row.workday_deep_work_min:.0f}min "
            f"({row.productivity_vs_baseline:g}x baseline)"
        ),
        payload={
            "sleep_hours": row.sleep_hours,
            "sleep_score": row.sleep_score,
            "sleep_quality": row.sleep_quality,
            "workday_active_hours": row.workday_active_hours,
            "workday_deep_work_min": row.workday_deep_work_min,
            "productivity_vs_baseline": row.productivity_vs_baseline,
        },
    )


__all__ = [
    "HealthMetricNode",
    "SleepProductivityLink",
    "SleepQualityNode",
    "build_health_evidence",
    "build_sleep_evidence",
    "build_sleep_productivity_links",
    "sleep_productivity_link_from_row",
]
