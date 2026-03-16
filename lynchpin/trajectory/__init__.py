"""Trajectory substrate built from recent raw local signals."""

from __future__ import annotations

from .anomaly import TrajectoryAnomaly, detect_anomalies
from .chains import TrajectoryChain, build_chains, build_chains_from_attributed
from .coverage import SignalCoverage, compute_coverage
from .day import TrajectoryDay, TrajectoryDayProject, summarize_days
from .episode import TrajectoryEpisode, detect_episodes
from .month import TrajectoryMonth
from .month import summarize_months as summarize_trajectory_months
from .period import TrajectoryPeriodSummary, summarize_months, summarize_period
from .quarter import TrajectoryQuarter, summarize_quarters
from .rules import AttributedSignal, SignalAttribution, classify_signal, classify_signals
from .signal import TrajectorySignal, iter_signals, load_signals, resolve_window
from .week import TrajectoryWeek, summarize_weeks
from .year import TrajectoryYear, summarize_years

__all__ = [
    "AttributedSignal",
    "SignalAttribution",
    "SignalCoverage",
    "TrajectoryAnomaly",
    "TrajectoryChain",
    "TrajectoryDay",
    "TrajectoryDayProject",
    "TrajectoryEpisode",
    "TrajectoryMonth",
    "TrajectoryPeriodSummary",
    "TrajectoryQuarter",
    "TrajectorySignal",
    "TrajectoryWeek",
    "TrajectoryYear",
    "build_chains",
    "build_chains_from_attributed",
    "classify_signal",
    "classify_signals",
    "compute_coverage",
    "detect_anomalies",
    "detect_episodes",
    "iter_signals",
    "load_signals",
    "resolve_window",
    "summarize_days",
    "summarize_months",
    "summarize_period",
    "summarize_quarters",
    "summarize_trajectory_months",
    "summarize_weeks",
    "summarize_years",
]
