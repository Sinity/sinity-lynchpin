"""Browser SQLite DB reader — Chromium (Chrome/Edge/Vivaldi) and Firefox.

Reads ``urls JOIN visits`` (Chromium) or ``moz_places JOIN moz_historyvisits``
(Firefox), converts browser-native epochs to UTC datetime, and yields
``WebHistoryVisit`` objects compatible with ``lynchpin.sources.web``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .web import WebHistoryVisit

__all__ = ["iter_browser_db_visits", "BROWSER_DB_KINDS"]

# WebKit/Chrome epoch: 1601-01-01T00:00:00Z in microseconds
_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_WEBKIT_EPOCH_TS = _WEBKIT_EPOCH.timestamp()

# UNIX epoch in seconds for Firefox microsecond conversion
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Chromium: visit_time is microseconds since 1601-01-01
_CHROMIUM_SQL = """
    SELECT v.visit_time, u.url, u.title
    FROM visits v
    JOIN urls u ON v.url = u.id
    ORDER BY v.visit_time
"""

# Firefox: visit_date is microseconds since 1970-01-01
_FIREFOX_SQL = """
    SELECT h.visit_date, p.url, p.title
    FROM moz_historyvisits h
    JOIN moz_places p ON h.place_id = p.id
    ORDER BY h.visit_date
"""


def _webkit_to_datetime(webkit_micros: int) -> datetime:
    """Convert Chromium WebKit epoch (microseconds since 1601-01-01) to UTC datetime."""
    return datetime.fromtimestamp(
        _WEBKIT_EPOCH_TS + webkit_micros / 1_000_000.0, tz=timezone.utc
    )


def _unix_micros_to_datetime(unix_micros: int) -> datetime:
    """Convert UNIX epoch microseconds to UTC datetime."""
    return datetime.fromtimestamp(unix_micros / 1_000_000.0, tz=timezone.utc)


BROWSER_DB_KINDS = ("chromium", "firefox")


def iter_browser_db_visits(
    path: Path,
    *,
    kind: str = "chromium",
    source_label: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[WebHistoryVisit]:
    """Yield WebHistoryVisit objects from a browser SQLite database.

    Args:
        path: Path to the SQLite database file.
        kind: ``"chromium"`` (Chrome/Edge/Vivaldi/Opera) or ``"firefox"``.
        source_label: Optional label for the ``source`` field.
        start, end: Optional timezone-aware, half-open SQL timestamp bounds.
    """
    if (start is None) != (end is None):
        raise ValueError("browser history requires both timestamp bounds")
    sql = _CHROMIUM_SQL if kind == "chromium" else _FIREFOX_SQL
    parameters: tuple[int, int] | tuple[()] = ()
    if start is not None and end is not None:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError(
                "browser history requires an increasing timezone-aware window"
            )
        epoch = _WEBKIT_EPOCH if kind == "chromium" else _UNIX_EPOCH

        def micros(value: datetime) -> int:
            delta = value.astimezone(timezone.utc) - epoch
            return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds

        column = "v.visit_time" if kind == "chromium" else "h.visit_date"
        sql = sql.replace(
            "    ORDER BY", f"    WHERE {column} >= ? AND {column} < ?\n    ORDER BY"
        )
        parameters = (micros(start), micros(end))
    convert = _webkit_to_datetime if kind == "chromium" else _unix_micros_to_datetime

    path = Path(path)
    label = source_label or f"{kind}:{path.name}"

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for ts_raw, url, title in conn.execute(sql, parameters):
            if not url:
                continue
            try:
                dt = convert(int(ts_raw))
            except (ValueError, OSError, OverflowError):
                continue
            yield WebHistoryVisit(
                timestamp=dt,
                url=str(url),
                title=str(title or ""),
                source=label,
            )
    finally:
        conn.close()
