"""Model-facing context packet builders."""

from __future__ import annotations

from .calendar import DaySummary, NarrativeRangeSummary, load_day_summaries, load_day_summary, summarize_range
from .life_timeline import (
    LifeMonthIntakeSummary,
    LifeMonthHealthSummary,
    LifeMonthLocationSummary,
    LifeMonthMailSummary,
    LifeMonthMoneySummary,
    LifeMonthNotesSummary,
    LifeMonthOutputSummary,
    LifeMonthSummary,
    LifeMonthTrajectorySummary,
    LifeMonthWorkSummary,
    build_intake_summary,
    build_health_summary,
    build_location_summary,
    build_mail_summary,
    build_month_summary,
    build_money_summary,
    build_notes_summary,
    build_output_summary,
    build_recent_trajectory_summaries,
    build_work_summary,
    render_markdown,
)
from .packets import build_recent_state

__all__ = [
    "DaySummary",
    "LifeMonthIntakeSummary",
    "LifeMonthHealthSummary",
    "LifeMonthLocationSummary",
    "LifeMonthMailSummary",
    "LifeMonthMoneySummary",
    "LifeMonthNotesSummary",
    "LifeMonthOutputSummary",
    "LifeMonthSummary",
    "LifeMonthTrajectorySummary",
    "LifeMonthWorkSummary",
    "NarrativeRangeSummary",
    "build_intake_summary",
    "build_health_summary",
    "build_location_summary",
    "build_mail_summary",
    "build_month_summary",
    "build_money_summary",
    "build_notes_summary",
    "build_output_summary",
    "build_recent_trajectory_summaries",
    "build_recent_state",
    "build_work_summary",
    "load_day_summaries",
    "load_day_summary",
    "render_markdown",
    "summarize_range",
]
