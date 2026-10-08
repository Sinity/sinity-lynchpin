"""Outlook CSV work email source — historical workplace period.

The existing Outlook CSV exports provide the dated inbox/sent events used by
Lynchpin's daily activity and communications products. PST extraction is not
supported: no current consumer requires PST-only evidence, and the historical
readpst route did not establish complete mailbox coverage.

The operator's name and address are loaded from an optional external
config (see _load_operator_identity) rather than hardcoded, same
pattern as raw_log.py's substance vocabulary. SVN username: michab.
306 emails total (164 inbox + 142 sent), Sep 2021 - Sep 2022.
"""

from __future__ import annotations

import csv
import email.utils
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from ..core.config import get_config
from ..core.coverage import CoverageBounds
from ..core.primitives import logical_date
from pathlib import Path
from typing import Iterator, Optional

from ..core.errors import SourceUnavailableError

PST_ROOT = Path("/realm/account/outlook/historical/jbr/raw")


def _load_operator_identity() -> tuple[str, str]:
    """Operator's name/email for this account, from optional external
    config -- same pattern as raw_log.py's substance vocabulary. Falls
    back to a generic placeholder if the config file is absent."""
    path = get_config().derived_root / "local-config" / "operator_identity.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            email_addr = raw.get("outlook_email")
            display = raw.get("outlook_display_name")
            if email_addr and display:
                return str(email_addr), str(display)
    except (OSError, json.JSONDecodeError):
        pass
    return "operator@example.com", "Operator"


OPERATOR_EMAIL, OPERATOR_DISPLAY = _load_operator_identity()


@dataclass(frozen=True)
class OutlookEmail:
    """One work email."""

    message_id: str
    subject: str
    sender: str  # display name or email
    sender_email: str
    recipients: tuple[str, ...]  # display names
    recipient_emails: tuple[str, ...]
    date: datetime
    body_preview: str  # first 500 chars of plain text body
    folder: str  # "inbox" | "sent" | "deleted"
    is_sent: bool


@dataclass(frozen=True)
class OutlookDayActivity:
    """Per-day email activity."""

    date: date
    inbox_count: int
    sent_count: int
    unique_correspondents: int


def _parse_date(s: str) -> Optional[datetime]:
    """Parse an RFC 2822 date string to UTC datetime."""
    try:
        tt = email.utils.parsedate_tz(s)
        if tt is None:
            return None
        return datetime(*tt[:6], tzinfo=timezone.utc) if tt[9] is None else datetime.fromtimestamp(
            email.utils.mktime_tz(tt), tz=timezone.utc
        )
    except Exception:
        return None


def iter_emails(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Iterator[OutlookEmail]:
    """Iterate dated work email events from Outlook CSV exports.

    Yields in chronological order. Filters by start/end if provided.
    """
    emails = [
        row for row in _iter_csv_emails()
        if (start is None or row.date >= start) and (end is None or row.date <= end)
    ]
    emails.sort(key=lambda e: e.date)

    for e in emails:
        if start and e.date < start:
            continue
        if end and e.date > end:
            continue
        yield e


def iter_pst_emails() -> Iterator[OutlookEmail]:
    """Report that direct PST access is unsupported by this source API."""
    raise SourceUnavailableError(
        "outlook_pst",
        path=str(PST_ROOT),
        reason="PST extraction is unsupported; use the Outlook CSV export route",
    )


_SENT_RE = re.compile(r"(?im)^\s*Sent:\s*(.+?)\s*$")


def _iter_csv_emails() -> Iterator[OutlookEmail]:
    """Read adjacent Outlook CSV exports.

    The CSV files do not expose a first-class date column, but the exported
    bodies include Outlook forward headers (`Sent: ...`) for the work emails
    this source covers. Rows without a parseable embedded date are skipped
    rather than assigned fabricated timestamps.
    """
    for csv_name, folder_label in (("inbox.CSV", "inbox"), ("sent.CSV", "sent")):
        path = PST_ROOT / csv_name
        if not path.exists():
            continue
        with path.open(encoding="cp1250", newline="") as handle:
            for idx, row in enumerate(csv.DictReader(handle)):
                body = row.get("Treść", "")
                match = _SENT_RE.search(body)
                if match is None:
                    continue
                sent_at = _parse_date(match.group(1))
                if sent_at is None:
                    continue
                recipients = _split_csv_people(row.get("Do: (imię/nazwisko)", ""))
                recipient_emails = _split_csv_people(row.get("Do: (adres)", ""))
                sender = row.get("Od: (imię/nazwisko)", "")
                sender_email = row.get("Od: (adres)", "")
                yield OutlookEmail(
                    message_id=f"{csv_name}:{idx}",
                    subject=row.get("Temat", ""),
                    sender=sender,
                    sender_email=sender_email,
                    recipients=recipients,
                    recipient_emails=recipient_emails,
                    date=sent_at,
                    body_preview=body.strip()[:500],
                    folder=folder_label,
                    is_sent=folder_label == "sent",
                )


def _split_csv_people(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(";") if item.strip())


def daily_activity(
    *,
    start: date,
    end: date,
) -> list[OutlookDayActivity]:
    """Per-day email activity summary."""
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    buckets: dict = defaultdict(  # type: ignore[type-arg]
        lambda: {"inbox_count": 0, "sent_count": 0, "correspondents": set()}
    )

    for eml in iter_emails(start=start_dt, end=end_dt):
        day = logical_date(eml.date)
        b = buckets[day]
        if eml.is_sent:
            b["sent_count"] += 1
            for addr in eml.recipient_emails:
                if addr:
                    b["correspondents"].add(addr)
        else:
            b["inbox_count"] += 1
            if eml.sender_email:
                b["correspondents"].add(eml.sender_email)

    result = []
    for day in sorted(buckets):
        b = buckets[day]
        result.append(
            OutlookDayActivity(
                date=day,
                inbox_count=b["inbox_count"],
                sent_count=b["sent_count"],
                unique_correspondents=len(b["correspondents"]),
            )
        )
    return result


def coverage_bounds() -> CoverageBounds | None:
    if not PST_ROOT.exists():
        return None
    try:
        first_dt, last_dt = date_range()
    except SourceUnavailableError:
        return None
    return CoverageBounds(
        source="outlook",
        first=first_dt.date(),
        last=last_dt.date(),
        kind="export",
    )


def date_range() -> tuple[datetime, datetime]:
    """Oldest and newest email dates."""
    emails = list(iter_emails())
    if not emails:
        raise SourceUnavailableError("outlook", reason="No emails found")
    return emails[0].date, emails[-1].date


def correspondent_stats(
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[str, int]]:
    """Top correspondents by email count."""
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc) if end else None
    counts: dict[str, int] = defaultdict(int)
    for eml in iter_emails(start=start_dt, end=end_dt):
        if eml.is_sent:
            for addr in eml.recipient_emails:
                if addr:
                    counts[addr] += 1
        else:
            if eml.sender_email:
                counts[eml.sender_email] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


__all__ = [
    "OutlookEmail",
    "OutlookDayActivity",
    "iter_emails",
    "iter_pst_emails",
    "daily_activity",
    "coverage_bounds",
    "date_range",
    "correspondent_stats",
]
