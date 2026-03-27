from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from ... import signals as signal_model
from ...signals import chains as signal_chains
from ...signals import rules as signal_rules
from ...context.period_rollups import summarize_months, summarize_quarters, summarize_weeks, summarize_years
from ...context.period_summaries import summarize_period
from ...context.signal_rollups import summarize_days
from .core import WarehouseContext, _parse_dt


@lru_cache(maxsize=8)
def _context_rows_window_cached(
    since_text: Optional[str],
    until_text: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    since = _parse_dt(since_text)
    until = _parse_dt(until_text)
    if start_date:
        since = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), tzinfo=local_tz)
    if end_date:
        until = datetime.combine(date.fromisoformat(end_date), datetime.min.time(), tzinfo=local_tz) + timedelta(days=1)
    return signal_model.resolve_window(start=since, end=until, days=signal_model.DEFAULT_LOOKBACK_DAYS)


def _context_rows_window(ctx: WarehouseContext) -> tuple[datetime, datetime]:
    return _context_rows_window_cached(
        ctx.since.isoformat() if ctx.since else None,
        ctx.until.isoformat() if ctx.until else None,
        ctx.start_date,
        ctx.end_date,
    )


@lru_cache(maxsize=8)
def _context_dataset(
    since_text: Optional[str],
    until_text: Optional[str],
) -> tuple[
    tuple[signal_rules.AttributedSignal, ...],
    tuple[signal_chains.ActivityChain, ...],
    tuple,
    tuple,
    tuple,
]:
    since = _parse_dt(since_text)
    until = _parse_dt(until_text)
    start, end = signal_model.resolve_window(start=since, end=until, days=signal_model.DEFAULT_LOOKBACK_DAYS)
    raw_signals = tuple(signal_model.load_signals(start=start, end=end, days=signal_model.DEFAULT_LOOKBACK_DAYS))
    attributed = tuple(signal_rules.classify_signals(raw_signals))
    chains = tuple(signal_chains.build_chains_from_attributed(attributed))
    days = tuple(
        summarize_days(
            signals=raw_signals,
            chains=chains,
            start=start,
            end=end,
            days=signal_model.DEFAULT_LOOKBACK_DAYS,
        )
    )
    period = summarize_period(days)
    return attributed, chains, days, period, raw_signals


@lru_cache(maxsize=8)
def _context_rollups_dataset(since_text: Optional[str], until_text: Optional[str]) -> tuple:
    _, _, days, _, raw_signals = _context_dataset(since_text, until_text)
    months = tuple(summarize_months(days, signals=raw_signals))
    quarters = tuple(summarize_quarters(months))
    years = tuple(summarize_years(quarters))
    weeks = tuple(summarize_weeks(days))
    return months, quarters, years, weeks


def _context_snapshot(ctx: WarehouseContext):
    start, end = _context_rows_window(ctx)
    attributed, chains, days, period, _raw = _context_dataset(start.isoformat(), end.isoformat())
    return attributed, chains, days, period


def _context_rollups_snapshot(ctx: WarehouseContext) -> tuple:
    start, end = _context_rows_window(ctx)
    return _context_rollups_dataset(start.isoformat(), end.isoformat())
