"""Side-by-side contrast between two periods at the same scale.

Used for week-over-week, month-over-month, quarter-over-quarter comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..trajectory.month import TrajectoryMonth
from ..trajectory.week import TrajectoryWeek


@dataclass(frozen=True)
class ContrastPacket:
    """Side-by-side comparison of current vs prior period."""
    scale: str                              # "day" | "week" | "month" | "quarter"
    current_key: str
    prior_key: str
    active_hours_delta: float
    recovery_hours_delta: float
    dominant_mode_shift: Optional[str]      # "X → Y" or None
    dominant_project_shift: Optional[str]  # "X → Y" or None
    dominant_topic_shift: Optional[str]    # "X → Y" or None
    chain_count_delta: int
    commit_count_delta: int
    direction: str                          # "up" | "down" | "flat" (based on active_hours_delta ±5%)

    def to_dict(self) -> dict[str, object]:
        return {
            "scale": self.scale,
            "current_key": self.current_key,
            "prior_key": self.prior_key,
            "active_hours_delta": round(self.active_hours_delta, 2),
            "recovery_hours_delta": round(self.recovery_hours_delta, 2),
            "dominant_mode_shift": self.dominant_mode_shift,
            "dominant_project_shift": self.dominant_project_shift,
            "dominant_topic_shift": self.dominant_topic_shift,
            "chain_count_delta": self.chain_count_delta,
            "commit_count_delta": self.commit_count_delta,
            "direction": self.direction,
        }


def build_contrast(
    current_period: Any,
    prior_period: Any,
    scale: str,
) -> ContrastPacket:
    """Build a contrast packet comparing two periods at the same scale.

    Args:
        current_period: TrajectoryDay, TrajectoryWeek, TrajectoryMonth, or TrajectoryQuarter
        prior_period: Same type as current_period
        scale: One of "day", "week", "month", "quarter"

    Returns:
        ContrastPacket with deltas and shifts between the two periods
    """
    # Determine current_key and prior_key based on scale
    if scale == "day":
        current_key = str(getattr(current_period, "date", "unknown"))
        prior_key = str(getattr(prior_period, "date", "unknown"))
    elif scale == "week":
        current_key = getattr(current_period, "iso_week", "unknown")
        prior_key = getattr(prior_period, "iso_week", "unknown")
    elif scale == "month":
        current_key = getattr(current_period, "month", "unknown")
        prior_key = getattr(prior_period, "month", "unknown")
    elif scale == "quarter":
        current_key = getattr(current_period, "quarter", "unknown")
        prior_key = getattr(prior_period, "quarter", "unknown")
    elif scale == "year":
        current_key = getattr(current_period, "year", "unknown")
        prior_key = getattr(prior_period, "year", "unknown")
    else:
        current_key = "unknown"
        prior_key = "unknown"

    # Active and recovery hours deltas
    current_active = getattr(current_period, "active_seconds", 0.0) / 3600.0
    prior_active = getattr(prior_period, "active_seconds", 0.0) / 3600.0
    active_hours_delta = current_active - prior_active

    current_recovery = getattr(current_period, "recovery_seconds", 0.0) / 3600.0
    prior_recovery = getattr(prior_period, "recovery_seconds", 0.0) / 3600.0
    recovery_hours_delta = current_recovery - prior_recovery

    # Mode shift
    current_mode = getattr(current_period, "dominant_mode", None)
    prior_mode = getattr(prior_period, "dominant_mode", None)
    dominant_mode_shift = None
    if current_mode and prior_mode and current_mode != prior_mode:
        dominant_mode_shift = f"{prior_mode} → {current_mode}"

    # Project shift
    current_project = getattr(current_period, "dominant_project", None)
    prior_project = getattr(prior_period, "dominant_project", None)
    dominant_project_shift = None
    if current_project and prior_project and current_project != prior_project:
        dominant_project_shift = f"{prior_project} → {current_project}"

    # Topic shift
    current_topic = getattr(current_period, "dominant_topic", None)
    prior_topic = getattr(prior_period, "dominant_topic", None)
    dominant_topic_shift = None
    if current_topic and prior_topic and current_topic != prior_topic:
        dominant_topic_shift = f"{prior_topic} → {current_topic}"

    # Chain and commit count deltas
    current_chain = getattr(current_period, "chain_count", 0)
    prior_chain = getattr(prior_period, "chain_count", 0)
    chain_count_delta = current_chain - prior_chain

    current_commit = getattr(current_period, "commit_count", 0)
    prior_commit = getattr(prior_period, "commit_count", 0)
    commit_count_delta = current_commit - prior_commit

    # Direction based on active hours delta (±5% threshold, minimum 0.1 hours)
    threshold = max(prior_active * 0.05, 0.1)
    if active_hours_delta > threshold:
        direction = "up"
    elif active_hours_delta < -threshold:
        direction = "down"
    else:
        direction = "flat"

    return ContrastPacket(
        scale=scale,
        current_key=current_key,
        prior_key=prior_key,
        active_hours_delta=active_hours_delta,
        recovery_hours_delta=recovery_hours_delta,
        dominant_mode_shift=dominant_mode_shift,
        dominant_project_shift=dominant_project_shift,
        dominant_topic_shift=dominant_topic_shift,
        chain_count_delta=chain_count_delta,
        commit_count_delta=commit_count_delta,
        direction=direction,
    )


def build_contrast_for_latest_week(
    weeks: list[TrajectoryWeek] | Any,
) -> Optional[ContrastPacket]:
    """Build a contrast packet for the two most recent weeks.

    Args:
        weeks: List of TrajectoryWeek objects

    Returns:
        ContrastPacket comparing current vs prior week, or None if insufficient data
    """
    if not isinstance(weeks, list) or len(weeks) < 2:
        return None

    current = weeks[-1]
    prior = weeks[-2]

    return build_contrast(current, prior, "week")


def build_contrast_for_latest_month(
    months: list[TrajectoryMonth] | Any,
) -> Optional[ContrastPacket]:
    """Build a contrast packet for the two most recent months.

    Args:
        months: List of TrajectoryMonth objects

    Returns:
        ContrastPacket comparing current vs prior month, or None if insufficient data
    """
    if not isinstance(months, list) or len(months) < 2:
        return None

    current = months[-1]
    prior = months[-2]

    return build_contrast(current, prior, "month")
