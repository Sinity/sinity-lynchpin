"""Freshness and trust accounting for narrative evidence surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..core.config import get_config

if TYPE_CHECKING:
    import duckdb


class TrustLevel(str, Enum):
    fresh = "fresh"
    lagging = "lagging"
    stale = "stale"
    unavailable = "unavailable"


@dataclass(frozen=True)
class SurfaceFreshness:
    surface: str
    date_column: str
    max_value: str | None
    row_count: int
    days_stale: int | None
    level: TrustLevel
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "date_column": self.date_column,
            "max_value": self.max_value,
            "row_count": self.row_count,
            "days_stale": self.days_stale,
            "level": self.level.value,
            "note": self.note,
        }


CORE_SURFACES: tuple[tuple[str, str, str | None], ...] = (
    ("processed_delivery_telemetry", "date", None),
    ("processed_project_attention", "date", None),
    ("processed_chat_activity", "date", "Prefer engaged_minutes over total_wall_minutes for effort claims."),
    ("processed_git_daily", "date", None),
    ("processed_focus_spans", "date", None),
    ("processed_focus_loops", "date", None),
    ("polylogue_session_profile", "last_message_at", None),
)


def open_warehouse_read_only(path: Path | None = None) -> "duckdb.DuckDBPyConnection":
    import duckdb

    cfg = get_config()
    db_path = Path(path or cfg.warehouse_db)
    return duckdb.connect(str(db_path), read_only=True)


def inspect_core_surface_freshness(
    *,
    conn: "duckdb.DuckDBPyConnection | None" = None,
    reference_date: date | None = None,
    surfaces: Iterable[tuple[str, str, str | None]] = CORE_SURFACES,
) -> list[SurfaceFreshness]:
    own_conn = conn is None
    if own_conn:
        conn = open_warehouse_read_only()
    assert conn is not None

    try:
        ref = reference_date or date.today()
        results: list[SurfaceFreshness] = []
        for surface, date_column, note in surfaces:
            results.append(_inspect_surface(conn, surface, date_column, ref, note))
        return results
    finally:
        if own_conn:
            conn.close()


def render_surface_freshness_markdown(rows: list[SurfaceFreshness]) -> str:
    if not rows:
        return ""
    lines = []
    for row in rows:
        max_value = row.max_value or "n/a"
        stale = "n/a" if row.days_stale is None else str(row.days_stale)
        note = f" — {row.note}" if row.note else ""
        lines.append(
            f"- {row.surface}: {row.level.value}, max={max_value}, rows={row.row_count}, stale_days={stale}{note}",
        )
    return "\n".join(lines)


def _inspect_surface(
    conn: "duckdb.DuckDBPyConnection",
    surface: str,
    date_column: str,
    reference_date: date,
    note: str | None,
) -> SurfaceFreshness:
    if not _table_exists(conn, surface):
        return SurfaceFreshness(
            surface=surface,
            date_column=date_column,
            max_value=None,
            row_count=0,
            days_stale=None,
            level=TrustLevel.unavailable,
            note=note or "table missing",
        )

    try:
        row_count = int(conn.execute(f"SELECT COUNT(*) FROM {surface}").fetchone()[0])
    except Exception as exc:
        return SurfaceFreshness(
            surface=surface,
            date_column=date_column,
            max_value=None,
            row_count=0,
            days_stale=None,
            level=TrustLevel.unavailable,
            note=f"{note or 'query failed'} ({exc})",
        )

    if row_count == 0:
        return SurfaceFreshness(
            surface=surface,
            date_column=date_column,
            max_value=None,
            row_count=0,
            days_stale=None,
            level=TrustLevel.unavailable,
            note=note or "no rows",
        )

    if not _column_exists(conn, surface, date_column):
        return SurfaceFreshness(
            surface=surface,
            date_column=date_column,
            max_value=None,
            row_count=row_count,
            days_stale=None,
            level=TrustLevel.unavailable,
            note=(note + "; " if note else "") + f"missing column {date_column}",
        )

    raw_max = conn.execute(f"SELECT MAX({date_column}) FROM {surface}").fetchone()[0]
    max_date = _normalize_to_date(raw_max)
    if max_date is None:
        return SurfaceFreshness(
            surface=surface,
            date_column=date_column,
            max_value=str(raw_max) if raw_max is not None else None,
            row_count=row_count,
            days_stale=None,
            level=TrustLevel.unavailable,
            note=(note + "; " if note else "") + "no usable freshness value",
        )

    days_stale = (reference_date - max_date).days
    level = _classify_staleness(days_stale)
    return SurfaceFreshness(
        surface=surface,
        date_column=date_column,
        max_value=max_date.isoformat(),
        row_count=row_count,
        days_stale=days_stale,
        level=level,
        note=note,
    )


def _classify_staleness(days_stale: int) -> TrustLevel:
    if days_stale <= 3:
        return TrustLevel.fresh
    if days_stale <= 14:
        return TrustLevel.lagging
    return TrustLevel.stale


def _normalize_to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _table_exists(conn: "duckdb.DuckDBPyConnection", table_name: str) -> bool:
    rows = conn.execute("SHOW TABLES").fetchall()
    return any(row[0] == table_name for row in rows)


def _column_exists(conn: "duckdb.DuckDBPyConnection", table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    return any(row[0] == column_name for row in rows)
