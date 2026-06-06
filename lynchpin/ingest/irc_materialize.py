"""Materialize raw WeeChat IRC logs into canonical message events."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.io import latest_mtime_iso
from ..core.primitives import logical_date
from ..sources.irc_raw import (
    irc_events_path,
    irc_manifest_path,
    irc_raw_root,
    iter_raw_messages,
    normalize_nick,
)
from .manifest_windows import merge_manifest_covered_dates


IRC_EVENTS_SCHEMA_VERSION = 1
IRCRow = dict[str, Any]


def materialize_irc_events(
    *,
    root: Path | None = None,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    raw_root = irc_raw_root() if root is None else root
    output = output or irc_events_path()
    if (start is None) != (end is None):
        raise ValueError("IRC materialization requires both start and end")
    if start is not None and end is not None and end <= start:
        raise ValueError("IRC materialization end must be after start")
    input_files = irc_input_files(raw_root)
    messages = sorted(iter_raw_messages(root=raw_root), key=lambda m: (m.timestamp, m.line_no))

    output.parent.mkdir(parents=True, exist_ok=True)
    window_rows: list[IRCRow] = []
    for msg in messages:
        day = logical_date(msg.timestamp)
        if start is not None and end is not None and not (start <= day < end):
            continue
        window_rows.append(_message_row(msg))

    if start is not None and end is not None:
        rows = _merge_existing_rows(output=output, start=start, end=end, window_rows=window_rows)
        covered_dates = _merge_covered_dates(manifest=irc_manifest_path(), start=start, end=end)
    else:
        rows = window_rows
        covered_dates = tuple(sorted({_row_logical_date(row) for row in rows}))

    rows.sort(key=lambda row: (str(row["timestamp"]), int(row.get("line_no") or 0)))
    timestamps = [_row_timestamp(row) for row in rows]
    channel_counts: dict[str, int] = {}
    for row in rows:
        channel = str(row.get("channel") or "")
        channel_counts[channel] = channel_counts.get(channel, 0) + 1

    _write_ndjson(output, rows)

    manifest = {
        "dataset": "comms.irc.events",
        "schema_version": IRC_EVENTS_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": len(rows),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": min(timestamps).date().isoformat() if timestamps else None,
        "last_timestamp_date": max(timestamps).date().isoformat() if timestamps else None,
        "date_boundary": "logical_06:00_local",
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive" if start is not None and end is not None else None,
        "channels": dict(sorted(channel_counts.items())),
        "raw_root": str(raw_root),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    irc_manifest_path().write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _message_row(msg: Any) -> IRCRow:
    return {
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


def _merge_existing_rows(
    *,
    output: Path,
    start: date,
    end: date,
    window_rows: list[IRCRow],
) -> list[IRCRow]:
    outside_window = [
        row for row in _read_existing_rows(output)
        if not (start <= _row_logical_date(row) < end)
    ]
    return [*outside_window, *window_rows]


def _read_existing_rows(path: Path) -> list[IRCRow]:
    if not path.exists():
        return []
    rows: list[IRCRow] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get("timestamp"):
                rows.append(payload)
    return rows


def _write_ndjson(path: Path, rows: list[IRCRow]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _row_timestamp(row: IRCRow) -> datetime:
    return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))


def _row_logical_date(row: IRCRow) -> date:
    return logical_date(_row_timestamp(row))


def _merge_covered_dates(*, manifest: Path, start: date, end: date) -> tuple[date, ...]:
    return merge_manifest_covered_dates(manifest=manifest, start=start, end=end)


def irc_input_files(root: Path | None = None) -> tuple[Path, ...]:
    raw_root = irc_raw_root() if root is None else root
    if not raw_root.exists():
        return ()
    return tuple(sorted(path for path in raw_root.rglob("*.log") if path.is_file()))


def _main() -> None:
    parser = argparse.ArgumentParser(description=materialize_irc_events.__doc__)
    parser.add_argument("--root", type=Path, default=None, help="raw IRC log root override")
    parser.add_argument("--output", type=Path, default=None, help="output NDJSON override")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    manifest = materialize_irc_events(root=args.root, output=args.output, start=args.start, end=args.end)
    print(json.dumps({"row_count": manifest["row_count"], "channels": len(manifest["channels"])}))


if __name__ == "__main__":
    _main()
