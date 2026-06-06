"""Gmail Takeout source — reads Gmail .mbox members from Google Takeout archives.

Materializes Gmail messages from raw Takeout archives (36 ``Mail`` members
across 28 archives, already inventoried by ``google_takeout.py``).

Parses ``.mbox`` members using Python's ``mailbox.mbox``. Deduplicates by
Message-ID across archives. Wires into the ``communications`` source pattern.

Graduated API:
  L0: raw GmailMessage iterator with archive discovery
  L1: deduplicated message stream
  Daily: daily_gmail_activity(start, end) → GmailDayActivity
"""

from __future__ import annotations

import json
import mailbox
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
import re

from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from .google_takeout import iter_member_bytes

__all__ = [
    "GmailMessage",
    "GmailDayActivity",
    "gmail_events_path",
    "gmail_manifest_path",
    "iter_gmail_messages",
    "iter_gmail_messages_deduped",
    "iter_materialized_gmail_messages",
    "daily_gmail_activity",
]

# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str | None
    sender: str
    recipients: tuple[str, ...]
    cc: tuple[str, ...]
    timestamp: datetime | None
    subject: str
    body_preview: str
    label: str  # Takeout archive label name (e.g. "Mail", "Important")
    archive_source: str
    size_bytes: int

    @property
    def date(self) -> date | None:
        return self.timestamp.date() if self.timestamp else None


@dataclass(frozen=True)
class GmailDayActivity:
    date: date
    message_count: int
    thread_count: int
    unique_correspondents: int
    outbound_count: int
    inbound_count: int

# ── Parsing ────────────────────────────────────────────────────────────────────


def _header_str(value) -> str:
    """Coerce an email.header.Header or string to str."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _extract_body_preview(msg) -> str:
    """Extract a short plain-text preview from the message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    return text[:200].replace("\n", " ").strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
            return text[:200].replace("\n", " ").strip()
    return ""


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        pass
    return None


def _parse_mbox_bytes(
    member_bytes: bytes, label: str, archive_source: str
) -> Iterator[GmailMessage]:
    """Parse Gmail messages from a .mbox byte payload."""
    with tempfile.NamedTemporaryFile(suffix=".mbox", delete=True) as tmp:
        tmp.write(member_bytes)
        tmp.flush()
        try:
            mbox = mailbox.mbox(tmp.name)
        except Exception:
            return
        for _key, msg in mbox.items():
            message_id = _header_str(msg.get("Message-ID", "")).strip()
            if not message_id:
                continue
            sender = _header_str(msg.get("From", ""))
            to_raw = _header_str(msg.get("To", ""))
            cc_raw = _header_str(msg.get("Cc", ""))
            recipients = tuple(
                addr.strip()
                for addr in (to_raw or "").split(",")
                if addr.strip()
            )
            cc = tuple(
                addr.strip()
                for addr in (cc_raw or "").split(",")
                if addr.strip()
            )
            yield GmailMessage(
                message_id=message_id,
                thread_id=_normalize_thread_id(
                    _header_str(msg.get("Thread-Id", None)) or _header_str(msg.get("References", None))
                ),
                sender=sender,
                recipients=recipients,
                cc=cc,
                timestamp=_parse_date(_header_str(msg.get("Date"))),
                subject=_header_str(msg.get("Subject", "")),
                body_preview=_extract_body_preview(msg),
                label=label,
                archive_source=archive_source,
                size_bytes=len(msg.as_bytes() if hasattr(msg, "as_bytes") else str(msg).encode()),
            )



def _normalize_thread_id(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    return cleaned if cleaned else None

# ── Public API ─────────────────────────────────────────────────────────────────


def gmail_events_path() -> Path:
    cfg = get_config()
    return cfg.exports_root / "google/processed/gmail/events.ndjson"


def gmail_manifest_path() -> Path:
    return gmail_events_path().with_suffix(".manifest.json")


def iter_materialized_gmail_messages(
    *,
    path: Optional[Path] = None,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[GmailMessage]:
    """Yield gmail messages from the canonical NDJSON product.

    When reading the configured canonical path, first converge the shared
    Google Takeout materialization bundle so ordinary reads do not silently
    reparse raw mbox archives.
    """
    target = path or gmail_events_path()
    if ensure and path is None:
        from ..materialization import ensure_materialized

        ensure_materialized("google_takeout", window=(start, end) if start and end else None)
    if not target.exists():
        raise FileNotFoundError(f"canonical Gmail Takeout product is missing: {target}")
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            ts_raw = payload.get("timestamp")
            ts = datetime.fromisoformat(ts_raw) if ts_raw else None
            if ts is not None and (start is not None or end is not None):
                d = ts.date()
                if start is not None and d < start:
                    continue
                if end is not None and d >= end:
                    continue
            yield GmailMessage(
                message_id=str(payload.get("message_id") or ""),
                thread_id=payload.get("thread_id"),
                sender=str(payload.get("sender") or ""),
                recipients=tuple(payload.get("recipients") or ()),
                cc=tuple(payload.get("cc") or ()),
                timestamp=ts,
                subject=str(payload.get("subject") or ""),
                body_preview=str(payload.get("body_preview") or ""),
                label=str(payload.get("label") or ""),
                archive_source=str(payload.get("archive_source") or ""),
                size_bytes=int(payload.get("size_bytes") or 0),
            )


def iter_gmail_messages(
    *,
    root: Optional[Path] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> Iterator[GmailMessage]:
    """Yield Gmail messages from all discovered Takeout archives.

    Uses ``iter_member_bytes`` to stream .mbox payloads from raw archives,
    then parses each with ``mailbox.mbox``.

    Not deduplicated — use ``iter_gmail_messages_deduped`` for cross-archive
    deduplication by Message-ID.
    """
    cfg = get_config()
    archive_root = root or cfg.exports_root / "google/raw/takeout"
    for member, payload in iter_member_bytes(
        root=archive_root,
        products={"Mail"},
        suffixes={".mbox"},
    ):
        for msg in _parse_mbox_bytes(
            payload,
            label=member.product,
            archive_source=str(member.archive),
        ):
            if msg.timestamp is not None and (start is not None or end is not None):
                d = msg.timestamp.date()
                if start is not None and d < start:
                    continue
                if end is not None and d >= end:
                    continue
            yield msg


def iter_gmail_messages_deduped(
    *,
    root: Optional[Path] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> Iterator[GmailMessage]:
    """Yield deduplicated Gmail messages (by Message-ID, first-wins)."""
    seen: set[str] = set()
    for msg in iter_gmail_messages(root=root, start=start, end=end):
        if msg.message_id in seen:
            continue
        seen.add(msg.message_id)
        yield msg


def daily_gmail_activity(
    *,
    start: date,
    end: date,
    root: Optional[Path] = None,
) -> list[GmailDayActivity]:
    """Daily Gmail activity rollup.

    Groups deduplicated messages by day. Messages without a timestamp are
    excluded (they are rare in practice).
    """
    by_date: dict[date, dict] = defaultdict(lambda: {
        "count": 0,
        "threads": set(),
        "correspondents": set(),
        "outbound": 0,
        "inbound": 0,
    })

    # Use the materialized NDJSON for configured reads; explicit root overrides
    # remain raw/source-level access for tests and one-off import diagnostics.
    if root is None:
        from ..materialization import ensure_materialized

        ensure_materialized("google_takeout", window=(start, end))
        iter_msgs = iter_materialized_gmail_messages(start=start, end=end, ensure=False)
    else:
        iter_msgs = iter_gmail_messages_deduped(root=root, start=start, end=end)

    for msg in iter_msgs:
        if msg.timestamp is None:
            continue
        d = msg.timestamp.date()
        bucket = by_date[d]
        bucket["count"] += 1
        if msg.thread_id:
            bucket["threads"].add(msg.thread_id)
        bucket["correspondents"].add(msg.sender)
        for r in msg.recipients:
            bucket["correspondents"].add(r)
        # Heuristic: if sender contains user-like patterns it's outbound.
        # A proper approach would check against known account emails.
        bucket["outbound" if _looks_outbound(msg.sender) else "inbound"] += 1

    result: list[GmailDayActivity] = []
    for d in sorted(by_date):
        b = by_date[d]
        result.append(GmailDayActivity(
            date=d,
            message_count=b["count"],
            thread_count=len(b["threads"]),
            unique_correspondents=len(b["correspondents"]),
            outbound_count=b["outbound"],
            inbound_count=b["inbound"],
        ))
    return result


# Operator email addresses that ever sent mail. Match exact addresses
# inside the From header, NOT substrings on the display name — the display
# name is set by the SENDING service, so e.g. ``"Sinity" <notifications@
# github.com>`` is a GitHub notification addressed TO sinity, not FROM sinity.
# An earlier heuristic matched 'sinity' anywhere and miscounted 45 such
# rows as outbound (vs. only 37 actual ezo.dev sends).
_OPERATOR_EMAIL_ADDRESSES: frozenset[str] = frozenset({
    "ezo.dev@gmail.com",
    "ilukbas@gmail.com",
    "sinity@substack.com",  # operator's substack publisher address
})

_EMAIL_ADDR_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _extract_email_address(header_value: str) -> str:
    """Extract the first email address from a From-style header."""
    if not header_value:
        return ""
    match = _EMAIL_ADDR_RE.search(header_value)
    return match.group(0).lower() if match else ""


def _looks_outbound(sender: str) -> bool:
    """True iff the From-header email address is one of the operator's
    known sending addresses.

    Doesn't match on display name because services freely set arbitrary
    display names (often the *recipient's* name) — substring matching
    on the display field produced 45 false positives out of 91 in
    one audit (GitHub notifications carrying ``"Sinity"`` as the From
    display while actually originating at ``notifications@github.com``).
    """
    addr = _extract_email_address(sender)
    return addr in _OPERATOR_EMAIL_ADDRESSES
