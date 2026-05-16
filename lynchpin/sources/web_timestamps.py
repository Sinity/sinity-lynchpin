"""Timestamp parsing helpers for webhistory sources."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


CHROME_CSV_LOCAL_TZ = ZoneInfo("Europe/Warsaw")

WEBHISTORY_TIMESTAMP_FIELDS = (
    "iso_time",
    "time",
    "visit_time",
    "visitTime",
    "lastVisitTime",
    "timestamp",
    "DateTime",
    "date",
)

_WEBHISTORY_NUMERIC_DIVISORS = (
    (10**18, 1_000_000_000.0),
    (10**15, 1_000_000.0),
    (10**12, 1_000.0),
)


# ---------------------------------------------------------------------------
# Timestamp parsing (from webhistory_common.py)
# ---------------------------------------------------------------------------


def parse_webhistory_timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return _parse_webhistory_numeric_timestamp(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = _parse_webhistory_iso_timestamp(text)
        if dt is not None:
            return dt
        dt = _parse_webhistory_slash_timestamp(text)
        if dt is not None:
            return dt
        try:
            return _parse_webhistory_numeric_timestamp(float(text))
        except ValueError:
            return None
    return None


def payload_timestamp(payload: dict[str, object]) -> datetime | None:
    for field in WEBHISTORY_TIMESTAMP_FIELDS:
        value = payload.get(field)
        if value in (None, ""):
            continue
        dt = parse_webhistory_timestamp(value)
        if dt is not None:
            return dt
    return None


def _parse_webhistory_numeric_timestamp(value: float) -> datetime | None:
    magnitude = abs(value)
    divisors = [1.0]
    for threshold, divisor in _WEBHISTORY_NUMERIC_DIVISORS:
        if magnitude >= threshold:
            divisors = [divisor, 1.0]
            break
    for divisor in divisors:
        try:
            return datetime.fromtimestamp(value / divisor, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            continue
    return None


def _parse_webhistory_iso_timestamp(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_webhistory_slash_timestamp(value: str) -> datetime | None:
    """Parse US-format date/time strings from Chrome CSV exports.

    Chrome CSV exports write timestamps in local time without timezone info.
    We treat them as local time (CHROME_CSV_LOCAL_TZ) and convert to UTC.
    """
    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
    ):
        try:
            naive = datetime.strptime(value, fmt)
            return naive.replace(tzinfo=CHROME_CSV_LOCAL_TZ).astimezone(timezone.utc)
        except ValueError:
            continue
    return None
