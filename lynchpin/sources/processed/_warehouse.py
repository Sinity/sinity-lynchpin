"""Read-only warehouse helpers for processed-source modules."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ...core.config import get_config

try:
    import duckdb
except ImportError:  # pragma: no cover - covered indirectly in callers
    duckdb = None  # type: ignore[assignment]


def query_rows(sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
    """Execute a read-only warehouse query and return raw rows."""
    if duckdb is None:
        return []
    db_path = get_config().warehouse_db
    if not db_path.exists():
        return []
    try:
        with duckdb.connect(str(db_path), read_only=True) as conn:
            result = conn.execute(sql, list(params or ()))
            return [tuple(row) for row in result.fetchall()]
    except Exception:
        return []


def coerce_date(value: Any) -> date | None:
    """Coerce DuckDB date-like values to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
