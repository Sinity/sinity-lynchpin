"""Google Takeout Chrome History JSON reader.

Handles the current ``History.json`` format: top-level ``"Session"`` array of
tab objects; each tab has ``navigation`` entries with ``timestamp_msec``
(UNIX epoch milliseconds), ``virtual_url``, ``title``, ``page_transition``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .web import WebHistoryVisit

__all__ = ["iter_takeout_chrome_visits"]


def _unix_to_datetime(unix_value: int, *, divisor: float = 1_000_000.0) -> datetime:
    """Convert UNIX epoch value (micros or millis) to UTC datetime."""
    return datetime.fromtimestamp(unix_value / divisor, tz=timezone.utc)


def iter_takeout_chrome_visits(
    path: Path,
    *,
    source_label: str | None = None,
) -> Iterator[WebHistoryVisit]:
    """Yield WebHistoryVisit objects from a Google Takeout Chrome History JSON.

    Ignores unsupported legacy Takeout shapes; backfill old exports before
    feeding them to Lynchpin.
    """
    label = source_label or f"takeout:{path.name}"
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        return

    if "Session" in data:
        yield from _parse_new_format(data["Session"], label)


def _parse_new_format(
    sessions: list[dict[str, object]], label: str
) -> Iterator[WebHistoryVisit]:
    for session in sessions:
        tab = session.get("tab")
        if not isinstance(tab, dict):
            continue
        navigations = tab.get("navigation")
        if not isinstance(navigations, list):
            continue
        for nav in navigations:
            if not isinstance(nav, dict):
                continue
            url = str(nav.get("virtual_url") or nav.get("url") or "").strip()
            if not url or url.startswith("chrome://") or url.startswith("about:"):
                continue
            try:
                dt = _unix_to_datetime(int(str(nav["timestamp_msec"])), divisor=1000.0)
            except (KeyError, ValueError, OSError, OverflowError):
                continue
            yield WebHistoryVisit(
                timestamp=dt,
                url=url,
                title=str(nav.get("title") or ""),
                source=label,
            )
