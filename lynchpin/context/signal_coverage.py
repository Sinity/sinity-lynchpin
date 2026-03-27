"""Typed per-day signal coverage derived from context day summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .summary_models import DaySummary


@dataclass(frozen=True)
class SignalCoverage:
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
    quality: str

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


def compute_coverage(day: DaySummary) -> SignalCoverage:
    sources = day.source_counts or {}
    source_names = tuple(sorted(sources))

    has_activitywatch = any(source.startswith("activitywatch.") for source in sources)
    has_terminal = any(source.startswith("instrumentation.") for source in sources)
    has_polylogue = bool(
        sources.get("polylogue.session")
        or sources.get("chatlog.transcript")
    )
    has_git = bool(sources.get("git.commit"))
    has_atuin = bool(sources.get("atuin.command"))
    has_web = bool(sources.get("activitywatch.web"))

    plane_count = sum(
        [
            has_activitywatch,
            has_terminal,
            has_polylogue,
            has_git,
            has_atuin,
            has_web,
        ]
    )

    return SignalCoverage(
        date=day.date,
        has_activitywatch=has_activitywatch,
        has_terminal=has_terminal,
        has_polylogue=has_polylogue,
        has_git=has_git,
        has_atuin=has_atuin,
        has_web=has_web,
        source_names=source_names,
        plane_count=plane_count,
        observed_hours=round(day.observed_seconds / 3600.0, 2),
        quality=_classify_quality(plane_count),
    )
