"""Materialize canonical ActivityWatch event products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..sources.activitywatch_dedup import dedup_and_merge
from ..sources.activitywatch_raw import (
    AWEvent,
    canonical_activitywatch_events_path,
    events_from_activitywatch_dbs,
)

BUCKET_PREFIXES = ("aw-watcher-window_", "aw-watcher-afk_", "aw-watcher-web_")


def materialize_activitywatch_events(
    *, output: Path | None = None, dedupe: bool = True
) -> dict[str, Any]:
    """Build the canonical AW events NDJSON.

    When ``dedupe`` (default), the raw events are cleaned via
    ``dedup_and_merge`` to repair two upstream defects: window/chrome
    zero-duration heartbeat spam (awatcher poll/pulsetime mismatch) and
    AFK duplicate-starttime cluster bug (PR #555 fix incomplete). See
    ``lynchpin/sources/activitywatch_dedup.py`` for the full rationale.

    Set ``dedupe=False`` to emit raw rows untouched (useful when
    diagnosing upstream bugs).
    """
    output = output or canonical_activitywatch_events_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    # Collect raw events first, then apply dedup per bucket. The dedup
    # function expects events to be grouped by bucket; sorting by
    # (bucket, start) achieves that.
    raw: list[AWEvent] = []
    for prefix in BUCKET_PREFIXES:
        for event in events_from_activitywatch_dbs(prefix):
            raw.append(event)
    raw.sort(key=lambda e: (e.bucket, e.start, e.end))

    if dedupe:
        cleaned = list(dedup_and_merge(raw))
    else:
        cleaned = raw

    # Sort the cleaned events by (bucket, start) for stable output, then
    # dedupe via dict-by-key in case dedup_and_merge left any logical
    # duplicates (it shouldn't, but keep the defensive layer).
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for event in cleaned:
        data_json = json.dumps(event.data, ensure_ascii=False, sort_keys=True)
        key = (
            event.bucket,
            event.start.isoformat(),
            event.end.isoformat(),
            data_json,
        )
        rows[key] = {
            "bucket": event.bucket,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "data": event.data,
        }

    ordered = [rows[key] for key in sorted(rows)]
    with output.open("w", encoding="utf-8") as handle:
        for row in ordered:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    starts = [
        datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
        for row in ordered
    ]
    manifest = {
        "dataset": "activitywatch.events",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": len(ordered),
        "first_date": min(starts).date().isoformat() if starts else None,
        "last_date": max(starts).date().isoformat() if starts else None,
        "bucket_prefixes": list(BUCKET_PREFIXES),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical ActivityWatch events")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_activitywatch_events(output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
