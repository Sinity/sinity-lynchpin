#!/usr/bin/env python3
"""Fix mtime/atime on Claude session JSONL files using internal timestamps.

Each JSONL line may have a top-level "timestamp" field (ISO 8601 UTC).
We set:
  - atime → first timestamp in file  (session start)
  - mtime → last timestamp in file   (session end / last activity)

This makes `ls -lt` sort by last activity and `ls -ltu` by session start.

Usage:
    python fix_session_timestamps.py [--dry-run] [path]

    path defaults to ~/.claude/projects/
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def extract_timestamps(path: Path) -> tuple[datetime | None, datetime | None]:
    """Extract first and last ISO timestamp from a JSONL file."""
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            if not isinstance(obj, dict):
                continue
            raw = obj.get("timestamp")
            if not raw or not isinstance(raw, str):
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if first_ts is None:
                    first_ts = dt
                last_ts = dt
            except ValueError:
                continue

    return first_ts, last_ts


def fix_file(path: Path, *, dry_run: bool = False) -> str | None:
    """Fix one file. Returns a status string, or None if skipped."""
    first_ts, last_ts = extract_timestamps(path)
    if first_ts is None or last_ts is None:
        return None

    atime_epoch = first_ts.timestamp()
    mtime_epoch = last_ts.timestamp()

    current_mtime = os.path.getmtime(path)
    # Skip if mtime already matches (within 2 seconds)
    if abs(current_mtime - mtime_epoch) < 2:
        return None

    if dry_run:
        old_mt = datetime.fromtimestamp(current_mtime).strftime("%Y-%m-%d %H:%M")
        new_mt = last_ts.astimezone().strftime("%Y-%m-%d %H:%M")
        new_at = first_ts.astimezone().strftime("%Y-%m-%d %H:%M")
        return f"  {path.name[:40]:40s}  mtime {old_mt} → {new_mt}  atime → {new_at}"

    os.utime(path, (atime_epoch, mtime_epoch))
    return "fixed"


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    root = Path(args[0]) if args else Path.home() / ".claude" / "projects"

    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    files = sorted(root.rglob("*.jsonl"))
    print(f"Scanning {len(files)} JSONL files under {root}")

    fixed = 0
    skipped = 0
    no_ts = 0

    for f in files:
        result = fix_file(f, dry_run=dry_run)
        if result is None:
            # Check if it was no-timestamp or already-correct
            first, last = extract_timestamps(f)
            if first is None:
                no_ts += 1
            else:
                skipped += 1
        elif result == "fixed":
            fixed += 1
        else:
            # dry_run output
            print(result)
            fixed += 1

    action = "would fix" if dry_run else "fixed"
    print(f"\n{action}: {fixed}  already correct: {skipped}  no timestamps: {no_ts}")


if __name__ == "__main__":
    main()
