"""Materialize unified communication events from supported message exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.communications import CommunicationEvent, communication_events_path, communication_manifest_path
from ..sources.exports_messenger import is_operator_sender, iter_fbmessenger_messages
from ..sources.themotte import input_files as themotte_input_files
from ..sources.themotte import iter_messages as iter_themotte_messages
from ..sources.themotte import iter_notifications as iter_themotte_notifications
from ._manifest import write_manifest

_SENT_RE = re.compile(r"^\s*(Sent|Wysłano|Date):\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_TEAMS_RE = re.compile(r"^(?P<stamp>[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d\d:\d\d:\d\d\s+GMT[+-]\d{4}).*?--\s*(?P<kind>\w+)\s*--\s*(?P<body>.*)$")
COMMUNICATION_EVENTS_SCHEMA_VERSION = 1


def materialize_communication_events(*, output: Path | None = None) -> dict[str, Any]:
    cfg = get_config()
    output = output or communication_events_path()
    input_files = communication_input_files(cfg)
    rows = list(_dedupe(_iter_all_events(cfg)))
    rows.sort(key=lambda row: (row.timestamp or datetime.min.replace(tzinfo=timezone.utc), row.source, row.event_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = asdict(row)
            payload["timestamp"] = row.timestamp.isoformat() if row.timestamp else None
            payload["recipients"] = list(row.recipients)
            payload["caveats"] = list(row.caveats)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    dated = [row.timestamp for row in rows if row.timestamp is not None]
    manifest = {
        "dataset": "comms.communication_events",
        "schema_version": COMMUNICATION_EVENTS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "row_count": len(rows),
        "first_date": min(dated).date().isoformat() if dated else None,
        "last_date": max(dated).date().isoformat() if dated else None,
        "sources": sorted({row.source for row in rows}),
        "missing_timestamp_rows": sum(1 for row in rows if row.timestamp is None),
        "teams_promoted_rows": 0,
        "teams_viability": _teams_viability(cfg.teams_root),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(communication_manifest_path(), manifest)
    return manifest


def communication_input_files(cfg: Any) -> tuple[Path, ...]:
    messenger = cfg.exports_root / "comms/facebook-messenger/processed/canonical/messages.ndjson"
    outlook = cfg.exports_root / "comms/outlook"
    teams = cfg.teams_root
    paths: list[Path] = []
    if messenger.exists():
        paths.append(messenger)
    if outlook.exists():
        paths.extend(path for path in outlook.rglob("*.CSV") if path.is_file())
    if teams.exists():
        paths.extend(path for path in teams.rglob("*.txt") if path.is_file())
    paths.extend(themotte_input_files())
    return tuple(sorted(paths))


def _iter_all_events(cfg: Any) -> Iterator[CommunicationEvent]:
    yield from _messenger_events(cfg.exports_root / "comms/facebook-messenger/processed/canonical/messages.ndjson")
    yield from _outlook_events(cfg.exports_root / "comms/outlook")
    yield from _themotte_events()


def _messenger_events(path: Path) -> Iterator[CommunicationEvent]:
    if not path.exists():
        return
    try:
        messages = iter_fbmessenger_messages(paths=[path])
    except FileNotFoundError:
        return
    for msg in messages:
        text = msg.text or ""
        participants = tuple(p for p in msg.participants if p != msg.sender)
        if is_operator_sender(msg.sender):
            direction = "outbound"
        elif msg.sender:
            direction = "inbound"
        else:
            direction = "unknown"
        yield _event(
            source="facebook_messenger",
            account="facebook",
            conversation_id=msg.thread_name,
            timestamp=msg.timestamp,
            direction=direction,
            sender=msg.sender,
            recipients=participants,
            subject=msg.thread_name,
            text=text,
            media_count=msg.media_count,
            raw_kind=msg.kind,
            raw_path=msg.source,
            confidence="high" if msg.timestamp else "low",
            caveats=() if msg.timestamp else ("missing_timestamp",),
        )


def _outlook_events(*roots: Path) -> Iterator[CommunicationEvent]:
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.CSV")):
            folder = path.stem.lower()
            direction = "outbound" if folder == "sent" else "inbound" if folder == "inbox" else "unknown"
            with path.open(encoding="cp1250", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle):
                    text = str(row.get("Treść") or "")
                    timestamp = _message_body_timestamp(text)
                    yield _event(
                        source="outlook",
                        account="jbr",
                        conversation_id=str(row.get("Temat") or ""),
                        timestamp=timestamp,
                        direction=direction,
                        sender=str(row.get("Od: (imię/nazwisko)") or row.get("Od: (adres)") or ""),
                        recipients=tuple(
                            item
                            for item in (
                                str(row.get("Do: (imię/nazwisko)") or ""),
                                str(row.get("Do: (adres)") or ""),
                            )
                            if item
                        ),
                        subject=str(row.get("Temat") or ""),
                        text=text,
                        media_count=0,
                        raw_kind=f"outlook_{folder}",
                        raw_path=str(path),
                        confidence="medium" if timestamp else "low",
                        caveats=() if timestamp else ("timestamp_in_export_missing",),
                    )


def _themotte_events() -> Iterator[CommunicationEvent]:
    cfg = get_config()
    operator = cfg.themotte_username
    for msg in iter_themotte_messages(username=operator):
        peer = msg.peer or msg.recipient or msg.author
        yield _event(
            source="themotte",
            account=operator,
            conversation_id=peer,
            timestamp=msg.created_at,
            direction="outbound" if msg.author == operator else "inbound",
            sender=msg.author,
            recipients=(msg.recipient,) if msg.recipient else (),
            subject=f"TheMotte PM with @{peer}" if peer else "TheMotte PM",
            text=msg.body,
            media_count=0,
            raw_kind="themotte_private_message",
            raw_path=msg.url,
            confidence="high",
            caveats=(),
        )
    for notif in iter_themotte_notifications(username=operator):
        yield _event(
            source="themotte",
            account=operator,
            conversation_id=notif.kind,
            timestamp=notif.created_at,
            direction="inbound",
            sender=notif.actor,
            recipients=(operator,),
            subject=notif.title,
            text=notif.text,
            media_count=0,
            raw_kind="themotte_notification",
            raw_path=notif.url,
            confidence="medium" if notif.created_at else "low",
            caveats=() if notif.created_at else ("missing_timestamp",),
        )


def _teams_viability(root: Path) -> dict[str, Any]:
    timestamped_app_log_rows = 0
    if not root.exists():
        return {"promoted": False, "timestamped_app_log_rows": 0, "reason": "Teams raw root is missing"}
    for path in sorted(root.rglob("*.txt")):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = _TEAMS_RE.match(line.strip())
                if not match:
                    continue
                if _parse_teams_timestamp(match.group("stamp")) is not None:
                    timestamped_app_log_rows += 1
    return {
        "promoted": False,
        "timestamped_app_log_rows": timestamped_app_log_rows,
        "reason": "Teams candidate files are desktop telemetry logs, not timestamped message/call/chat events",
    }


def _message_body_timestamp(text: str) -> datetime | None:
    match = _SENT_RE.search(text)
    if not match:
        return None
    value = match.group(2).strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_teams_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%a %b %d %Y %H:%M:%S GMT%z")
    except ValueError:
        return None


def _event(
    *,
    source: str,
    account: str,
    conversation_id: str,
    timestamp: datetime | None,
    direction: str,
    sender: str,
    recipients: tuple[str, ...],
    subject: str,
    text: str,
    media_count: int,
    raw_kind: str,
    raw_path: str,
    confidence: str,
    caveats: tuple[str, ...],
) -> CommunicationEvent:
    excerpt = " ".join(text.split())[:240]
    digest = hashlib.sha1(
        f"{source}\0{account}\0{conversation_id}\0{timestamp}\0{sender}\0{subject}\0{excerpt}".encode(
            "utf-8", errors="replace"
        )
    ).hexdigest()
    return CommunicationEvent(
        event_id=digest,
        source=source,
        account=account,
        conversation_id=conversation_id,
        timestamp=timestamp,
        direction=direction,
        sender=sender,
        recipients=recipients,
        subject=subject,
        text_excerpt=excerpt,
        text_length=len(text),
        media_count=media_count,
        raw_kind=raw_kind,
        raw_path=raw_path,
        confidence=confidence,
        caveats=caveats,
    )


def _dedupe(rows: Iterator[CommunicationEvent]) -> Iterator[CommunicationEvent]:
    seen: set[str] = set()
    for row in rows:
        if row.event_id in seen:
            continue
        seen.add(row.event_id)
        yield row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical communication events")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    sys.stdout.write(json.dumps(materialize_communication_events(output=args.output), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
