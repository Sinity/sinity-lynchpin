"""Materialize Gmail .mbox archives into canonical NDJSON product.

Walks every ``Mail`` member across the Google Takeout archive set
(``exports/google/raw/takeout``), parses messages with ``mailbox.mbox``,
dedupes by ``Message-ID``, and writes one JSON row per message to
``exports/google/processed/gmail/events.ndjson`` with a sibling manifest.

Subsequent reads via ``iter_materialized_gmail_messages`` avoid the .mbox
reparse penalty (28 archives × 36 Mail members → ~minutes of mbox decode
per invocation otherwise).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from .google_takeout_materialize import google_takeout_input_files
from ..sources.gmail_takeout import (
    GmailMessage,
    gmail_events_path,
    gmail_manifest_path,
    iter_gmail_messages_deduped,
)

GMAIL_EVENTS_SCHEMA_VERSION = 1


def materialize_gmail_events(
    *, root: Path | None = None, output: Path | None = None
) -> dict[str, Any]:
    cfg = get_config()
    archive_root = root or cfg.exports_root / "google/raw/takeout"
    output = output or gmail_events_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    input_files = google_takeout_input_files(archive_root)

    row_count = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    label_counts: Counter[str] = Counter()
    skipped_no_timestamp = 0

    with output.open("w", encoding="utf-8") as handle:
        for msg in iter_gmail_messages_deduped(root=archive_root):
            if msg.timestamp is None:
                skipped_no_timestamp += 1
                continue
            handle.write(
                json.dumps(_message_payload(msg), ensure_ascii=False, sort_keys=True)
                + "\n"
            )
            row_count += 1
            ts_norm = _normalize(msg.timestamp)
            if first_ts is None or ts_norm < first_ts:
                first_ts = ts_norm
            if last_ts is None or ts_norm > last_ts:
                last_ts = ts_norm
            label_counts[msg.label] += 1

    manifest = {
        "dataset": "comms.gmail.events",
        "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": row_count,
        "first_date": first_ts.date().isoformat() if first_ts else None,
        "last_date": last_ts.date().isoformat() if last_ts else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "labels": dict(sorted(label_counts.items())),
        "skipped_no_timestamp": skipped_no_timestamp,
        "archive_root": str(archive_root),
    }
    gmail_manifest_path().write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _normalize(ts: datetime) -> datetime:
    """Coerce to UTC so naive + aware timestamps compare cleanly."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _message_payload(msg: GmailMessage) -> dict[str, Any]:
    return {
        "message_id": msg.message_id,
        "thread_id": msg.thread_id,
        "sender": msg.sender,
        "recipients": list(msg.recipients),
        "cc": list(msg.cc),
        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        "subject": msg.subject,
        "body_preview": msg.body_preview,
        "label": msg.label,
        "archive_source": msg.archive_source,
        "size_bytes": msg.size_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=materialize_gmail_events.__doc__)
    parser.add_argument("--root", type=Path, default=None, help="archive root override")
    parser.add_argument("--output", type=Path, default=None, help="output NDJSON override")
    args = parser.parse_args(argv)
    manifest = materialize_gmail_events(root=args.root, output=args.output)
    sys.stdout.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
