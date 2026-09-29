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
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.cache import input_versions
from ..core.errors import MaterializationError, SourceUnavailableError
from .google_takeout_materialize import google_takeout_input_files
from ..sources.gmail_takeout import (
    GmailMessage,
    gmail_events_path,
    gmail_manifest_path,
    iter_gmail_messages_deduped,
)
from ._manifest import atomic_write_ndjson, write_manifest

GMAIL_EVENTS_SCHEMA_VERSION = 2


def materialize_gmail_events(
    *, root: Path | None = None, output: Path | None = None
) -> dict[str, Any]:
    cfg = get_config()
    archive_root = root or cfg.accounts_root / "google/raw/takeout"
    canonical_output = gmail_events_path()
    output = output or canonical_output
    output.parent.mkdir(parents=True, exist_ok=True)
    if not archive_root.is_dir():
        raise SourceUnavailableError("gmail_takeout", path=str(archive_root), reason="archive root is unavailable")
    input_files = google_takeout_input_files(archive_root)
    observed_versions = input_versions(input_files)
    if any(item["stat"] is None for item in observed_versions):
        raise SourceUnavailableError("gmail_takeout", path=str(archive_root), reason="selected archive is unavailable")
    latest_mtime_ns = max((item["stat"][3] for item in observed_versions), default=None)

    row_count = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    label_counts: Counter[str] = Counter()
    unknown_timestamp_count = 0

    def message_rows() -> Iterator[dict[str, Any]]:
        nonlocal row_count, first_ts, last_ts, unknown_timestamp_count
        for msg in iter_gmail_messages_deduped(root=archive_root):
            if msg.timestamp is None:
                unknown_timestamp_count += 1
            yield _message_payload(msg)
            row_count += 1
            if msg.timestamp is not None:
                ts_norm = _normalize(msg.timestamp)
                if first_ts is None or ts_norm < first_ts:
                    first_ts = ts_norm
                if last_ts is None or ts_norm > last_ts:
                    last_ts = ts_norm
            label_counts[msg.label] += 1

    with tempfile.TemporaryDirectory(prefix=".gmail-events-", dir=output.parent) as staging:
        staged_output = Path(staging) / output.name
        atomic_write_ndjson(staged_output, message_rows())
        if input_versions(input_files) != observed_versions or google_takeout_input_files(archive_root) != input_files:
            raise MaterializationError("comms.gmail.events", reason="Takeout inputs changed while reading")
        manifest = {
            "dataset": "comms.gmail.events",
            "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
            "materialized_path": str(output),
            "row_count": row_count,
            "first_date": first_ts.date().isoformat() if first_ts else None,
            "last_date": last_ts.date().isoformat() if last_ts else None,
            "input_files": [str(path) for path in input_files],
            "input_file_count": len(input_files),
            "input_latest_mtime": (
                datetime.fromtimestamp(latest_mtime_ns / 1_000_000_000, timezone.utc).astimezone().isoformat()
                if latest_mtime_ns is not None else None
            ),
            "labels": dict(sorted(label_counts.items())),
            "unknown_timestamp_count": unknown_timestamp_count,
            "archive_root": str(archive_root),
            "input_versions": observed_versions,
        }
        if output == canonical_output:
            staged_manifest = Path(staging) / "manifest.json"
            write_manifest(staged_manifest, manifest)
        os.replace(staged_output, output)
        if output == canonical_output:
            os.replace(staged_manifest, gmail_manifest_path())
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
        "native_message_id": msg.native_message_id,
        "date_status": msg.date_status,
        "native_labels": list(msg.native_labels),
        "archive_member": msg.archive_member,
        "occurrence_index": msg.occurrence_index,
        "headers": [list(pair) for pair in msg.headers],
        "mime_parts": list(msg.mime_parts),
        "body": msg.body,
        "raw_message_base64": msg.raw_message_base64,
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
