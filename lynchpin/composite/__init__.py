"""Cross-source composite views — merge data from 2+ sources.

Every module here reads from at least two data sources and produces
a composite view: chronological timelines, day-level statistics,
delivery telemetry, activity segmentation, and intraday profiles.

Dependency rule: composite/ imports from core/ and sources/ only.
It does NOT import from enrich/ or narrative/.
"""

from lynchpin.composite.timeline import timeline, work_sessions, TimelineEvent, WorkSession
from lynchpin.composite.statistics import build_day_features, full_analysis, DayFeatures
from lynchpin.composite.delivery import daily_delivery, DeliveryTelemetry
from lynchpin.composite.segments import segment_day, segment_range, transition_bigrams, DaySegmentation
from lynchpin.composite.intraday import intraday_profile, clock_hour_profile, IntradayProfile
from lynchpin.composite.day_brief import day_summary, DaySummary, render_day_summary

__all__ = [
    "timeline", "work_sessions", "TimelineEvent", "WorkSession",
    "build_day_features", "full_analysis", "DayFeatures",
    "daily_delivery", "DeliveryTelemetry",
    "segment_day", "segment_range", "transition_bigrams", "DaySegmentation",
    "intraday_profile", "clock_hour_profile", "IntradayProfile",
    "day_summary", "DaySummary", "render_day_summary",
]
