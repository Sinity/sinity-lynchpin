"""Per-day signal coverage model.

Provides a typed representation of which signal planes are present
for each day, replacing the untyped dict in TrajectoryDay.coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .day import TrajectoryDay


@dataclass(frozen=True)
class SignalCoverage:
    """Per-day signal plane presence and quality."""

    date: date
    has_activitywatch: bool
    has_terminal: bool
    has_polylogue: bool
    has_git: bool
    has_atuin: bool
    has_web: bool
    source_names: tuple[str, ...]
    plane_count: int
    observed_hours: float
    quality: str  # "rich" (4+), "moderate" (2-3), "sparse" (1), "empty" (0)

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "has_activitywatch": self.has_activitywatch,
            "has_terminal": self.has_terminal,
            "has_polylogue": self.has_polylogue,
            "has_git": self.has_git,
            "has_atuin": self.has_atuin,
            "has_web": self.has_web,
            "source_names": list(self.source_names),
            "plane_count": self.plane_count,
            "observed_hours": round(self.observed_hours, 2),
            "quality": self.quality,
        }


def _classify_quality(plane_count: int) -> str:
    if plane_count >= 4:
        return "rich"
    if plane_count >= 2:
        return "moderate"
    if plane_count >= 1:
        return "sparse"
    return "empty"


def compute_coverage(day: TrajectoryDay) -> SignalCoverage:
    """Derive typed coverage from a TrajectoryDay's source_counts."""
    sources = day.source_counts or {}
    source_names = sorted(sources.keys())

    has_aw = any(s.startswith("activitywatch.") for s in sources)
    has_terminal = any(s.startswith("instrumentation.") for s in sources)
    has_polylogue = "polylogue.session" in sources
    has_git = "git.commit" in sources
    has_atuin = "atuin.command" in sources
    has_web = "activitywatch.web" in sources

    planes = sum([has_aw, has_terminal, has_polylogue, has_git, has_atuin, has_web])

    return SignalCoverage(
        date=day.date,
        has_activitywatch=has_aw,
        has_terminal=has_terminal,
        has_polylogue=has_polylogue,
        has_git=has_git,
        has_atuin=has_atuin,
        has_web=has_web,
        source_names=tuple(source_names),
        plane_count=planes,
        observed_hours=round((day.active_seconds + day.recovery_seconds) / 3600.0, 2),
        quality=_classify_quality(planes),
    )
