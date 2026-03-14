"""Reusable metric computations for Lynchpin views and dashboards.

Modules:
- focus: AFK-adjusted focus spans, app category classification
- git: churn per window, net LoC, commit density
- health: sleep quality score, rest duration
- productivity: command density, chat token density
"""

from .focus import afk_split, focus_minutes, window_label, duration_minutes
from .git import git_summary, GitMetrics
from .health import sleep_summary, SleepMetrics
from .productivity import commands_by_category, categorise_command

__all__ = [
    "afk_split",
    "focus_minutes",
    "window_label",
    "duration_minutes",
    "git_summary",
    "GitMetrics",
    "sleep_summary",
    "SleepMetrics",
    "commands_by_category",
    "categorise_command",
]
