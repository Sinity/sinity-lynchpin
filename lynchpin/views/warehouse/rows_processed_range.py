from __future__ import annotations

from datetime import date, datetime, timedelta

from .core import WarehouseContext


def _resolve_date_range(ctx: WarehouseContext) -> tuple[date | None, date | None]:
    start_d: date | None = None
    end_d: date | None = None
    if ctx.start_date:
        start_d = date.fromisoformat(ctx.start_date)
    elif ctx.since:
        start_d = ctx.since.date()
    if ctx.end_date:
        end_d = date.fromisoformat(ctx.end_date)
    elif ctx.until:
        end_d = ctx.until.date()
    return start_d, end_d


def _bounded_date_range(ctx: WarehouseContext) -> tuple[date, date]:
    start_d, end_d = _resolve_date_range(ctx)
    return start_d or date(2020, 1, 1), end_d or date(2030, 1, 1)


def _resolve_datetime_range(ctx: WarehouseContext) -> tuple[datetime, datetime]:
    start_d, end_d = _bounded_date_range(ctx)
    dt_start = datetime(start_d.year, start_d.month, start_d.day)
    dt_end = datetime(end_d.year, end_d.month, end_d.day) + timedelta(days=1)
    return dt_start, dt_end
