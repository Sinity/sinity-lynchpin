"""Materialize canonical ActivityWatch event products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..sources.activitywatch_raw import (
    canonical_activitywatch_events_path,
    events_from_activitywatch_dbs,
)

BUCKET_PREFIXES = ("aw-watcher-window_", "aw-watcher-afk_", "aw-watcher-web_")


def materialize_activitywatch_events(*, output: Path | None = None) -> dict[str, Any]:
    output = output or canonical_activitywatch_events_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for prefix in BUCKET_PREFIXES:
        for event in events_from_activitywatch_dbs(prefix):
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
