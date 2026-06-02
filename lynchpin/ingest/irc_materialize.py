"""Materialize raw WeeChat IRC logs into canonical message events."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..sources.irc_raw import (
    irc_events_path,
    irc_manifest_path,
    irc_raw_root,
    iter_messages,
    normalize_nick,
)


def materialize_irc_events(
    *, root: Path | None = None, output: Path | None = None
) -> dict[str, Any]:
    raw_root = irc_raw_root() if root is None else root
    output = output or irc_events_path()
    messages = sorted(iter_messages(root=raw_root), key=lambda m: (m.timestamp, m.line_no))

    output.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    channel_counts: dict[str, int] = {}

    with output.open("w", encoding="utf-8") as handle:
        for msg in messages:
            payload = {
                "timestamp": msg.timestamp.isoformat(),
                "speaker_raw": msg.speaker,
                "speaker_canonical": normalize_nick(msg.speaker),
                "text": msg.text,
                "channel": msg.channel,
                "source_file": msg.source_file,
                "line_no": msg.line_no,
                "is_meta": msg.is_meta,
                "word_count": msg.word_count,
            }
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            row_count += 1
            if first_ts is None or msg.timestamp < first_ts:
                first_ts = msg.timestamp
            if last_ts is None or msg.timestamp > last_ts:
                last_ts = msg.timestamp
            channel_counts[msg.channel] = channel_counts.get(msg.channel, 0) + 1

    manifest = {
        "dataset": "comms.irc.events",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": row_count,
        "first_date": first_ts.date().isoformat() if first_ts else None,
        "last_date": last_ts.date().isoformat() if last_ts else None,
        "channels": dict(sorted(channel_counts.items())),
        "raw_root": str(raw_root),
    }
    irc_manifest_path().write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _main() -> None:
    parser = argparse.ArgumentParser(description=materialize_irc_events.__doc__)
    parser.add_argument("--root", type=Path, default=None, help="raw IRC log root override")
    parser.add_argument("--output", type=Path, default=None, help="output NDJSON override")
    args = parser.parse_args()
    manifest = materialize_irc_events(root=args.root, output=args.output)
    print(json.dumps({"row_count": manifest["row_count"], "channels": len(manifest["channels"])}))


if __name__ == "__main__":
    _main()
