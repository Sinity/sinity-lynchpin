"""Trajectory substrate built from recent raw local signals."""

from __future__ import annotations

from .chains import TrajectoryChain, build_chains, build_chains_from_attributed
from .day import TrajectoryDay, TrajectoryDayProject, summarize_days
from .period import TrajectoryPeriodSummary, summarize_months, summarize_period
from .rules import AttributedSignal, SignalAttribution, classify_signal, classify_signals
from .signal import TrajectorySignal, iter_signals, load_signals, resolve_window

__all__ = [
    "AttributedSignal",
    "SignalAttribution",
    "TrajectoryChain",
    "TrajectoryDay",
    "TrajectoryDayProject",
    "TrajectoryPeriodSummary",
    "TrajectorySignal",
    "build_chains",
    "build_chains_from_attributed",
    "classify_signal",
    "classify_signals",
    "iter_signals",
    "load_signals",
    "resolve_window",
    "summarize_months",
    "summarize_days",
    "summarize_period",
]
