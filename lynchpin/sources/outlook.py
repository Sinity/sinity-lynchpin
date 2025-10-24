"""Outlook PST work email source — historical workplace period.

Data: /realm/data/exports/comms/outlook/historical/jbr/raw/
      (inbox_backup.pst, sent_backup.pst, deleted_backup.pst)

Uses libpst (readpst) to extract PST to mbox, then Python's mailbox
module to parse. The first run extracts PST → /tmp/outlook_extract/;
subsequent runs reuse the cached mbox files.

The operator's name and address are loaded from an optional external
config (see _load_operator_identity) rather than hardcoded, same
pattern as raw_log.py's substance vocabulary. SVN username: michab.
306 emails total (164 inbox + 142 sent), Sep 2021 - Sep 2022.
"""

from __future__ import annotations

import csv
import email.utils
import json
import mailbox
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from ..core.config import get_config
from ..core.coverage import CoverageBounds
from ..core.primitives import logical_date
from pathlib import Path
from typing import Iterator, Optional

from ..core.errors import SourceUnavailableError

PST_ROOT = Path("/realm/data/exports/comms/outlook/historical/jbr/raw")
MBOX_CACHE = Path("/tmp/outlook_extract/Plik danych programu Outlook")


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


def _ensure_extracted() -> Path:
    """Extract PST files if not already cached. Returns mbox cache root."""
    if MBOX_CACHE.exists() and any(MBOX_CACHE.rglob("mbox")):
        return MBOX_CACHE

    MBOX_CACHE.mkdir(parents=True, exist_ok=True)

    for pst_name, folder in [
        ("inbox_backup.pst", "Skrzynka odbiorcza"),
        ("sent_backup.pst", "Elementy wysłane"),
    ]:
        pst_path = PST_ROOT / pst_name
        if not pst_path.exists():
            continue
        subprocess.run(
            ["nix-shell", "-p", "libpst", "--command",
             f"readpst -o {MBOX_CACHE} -r {pst_path}"],
            capture_output=True,
            timeout=60,
        )

    return MBOX_CACHE


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
    """Iterate all work emails from extracted PST files.

    Yields in chronological order. Filters by start/end if provided.
    """
    try:
        cache = _ensure_extracted()
    except Exception:
        return

    emails = []
    for folder_name in ("Skrzynka odbiorcza", "Elementy wysłane"):
        mbox_path = cache / folder_name / "mbox"
        if not mbox_path.exists():
            continue
        folder_label = "inbox" if "odbiorcza" in folder_name.lower() else "sent"

        mbox = mailbox.mbox(str(mbox_path))
        for key, msg in mbox.items():
            try:
                date_str = msg.get("Date", "")
                date = _parse_date(date_str)
                if date is None:
                    continue
                if start and date < start:
                    continue
                if end and date > end:
                    continue

                subject_raw = msg.get("Subject", "")
                # Decode RFC 2047 encoded headers
                subject = ""
                for part, charset in email.header.decode_header(subject_raw):
                    if isinstance(part, bytes):
                        try:
                            subject += part.decode(charset or "utf-8", errors="replace")
                        except Exception:
                            subject += part.decode("utf-8", errors="replace")
                    else:
                        subject += str(part)

                sender = msg.get("From", "")
                sender_name, sender_addr = email.utils.parseaddr(sender)

                to_raw = msg.get("To", "")
                recipients = []
                recipient_emails = []
                for name, addr in email.utils.getaddresses([to_raw]):
                    recipients.append(name or addr)
                    recipient_emails.append(addr)

                # Get plain text body preview
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body = payload.decode("utf-8", errors="replace")[:500]
                                    break
                            except Exception:
                                pass
                else:
                    try:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")[:500]
                    except Exception:
                        pass

                is_sent = folder_label == "sent"
                emails.append(
                    OutlookEmail(
                        message_id=msg.get("Message-ID", ""),
                        subject=subject,
                        sender=sender_name or sender_addr,
                        sender_email=sender_addr,
                        recipients=tuple(recipients),
                        recipient_emails=tuple(recipient_emails),
                        date=date,
                        body_preview=body.strip(),
                        folder=folder_label,
                        is_sent=is_sent,
                    )
                )
            except Exception:
                continue

    if not emails:
        for email_row in _iter_csv_emails():
            if start and email_row.date < start:
                continue
            if end and email_row.date > end:
                continue
            emails.append(email_row)

    emails.sort(key=lambda e: e.date)

    for e in emails:
        if start and e.date < start:
            continue
        if end and e.date > end:
            continue
        yield e


_SENT_RE = re.compile(r"(?im)^\s*Sent:\s*(.+?)\s*$")


def _iter_csv_emails() -> Iterator[OutlookEmail]:
    """Fallback to the adjacent Outlook CSV exports when readpst is absent.

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
    "daily_activity",
    "coverage_bounds",
    "date_range",
    "correspondent_stats",
]
