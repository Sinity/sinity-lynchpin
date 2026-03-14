"""Sleep quality score and rest duration metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SleepMetrics:
    """Aggregated sleep metrics for a night."""
    total_hours: float
    segments: int
    avg_score: Optional[float]

    @property
    def quality_label(self) -> str:
        if self.avg_score is None:
            return "unknown"
        if self.avg_score >= 80:
            return "good"
        if self.avg_score >= 60:
            return "fair"
        return "poor"


def sleep_summary(entry) -> Optional[SleepMetrics]:
    """Compute sleep metrics from a SleepEntry (or None if no data)."""
    if entry is None:
        return None
    total_hours = (getattr(entry, "total_minutes", 0) or 0.0) / 60.0
    segments = len(getattr(entry, "segments", []))
    score = getattr(entry, "avg_score", None)
    return SleepMetrics(
        total_hours=round(total_hours, 2),
        segments=segments,
        avg_score=score,
    )
