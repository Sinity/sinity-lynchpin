#!/usr/bin/env python3
"""Summarise IRC channels and log coverage."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, Tuple

from irc_log_utils import captures_root, channel_from_path, iter_entries, iter_log_files


def main(root: Path) -> None:
    file_counts: Dict[str, int] = defaultdict(int)
    entry_counts: Dict[str, int] = defaultdict(int)
    span: Dict[str, Tuple[str, str]] = {}

    files = list(iter_log_files(root))
    if not files:
        print("No log files found.")
        return

    for path in files:
        channel = channel_from_path(path) or "?"
        file_counts[channel] += 1
        first_entry = last_entry = None
        for entry in iter_entries([path]):
            entry_counts[channel] += 1
            first_entry = first_entry or entry
            last_entry = entry
        if first_entry and last_entry:
            span[channel] = (
                span.get(channel, (first_entry.timestamp.isoformat(), last_entry.timestamp.isoformat()))[0],
                last_entry.timestamp.isoformat(),
            )

    print(f"Found {len(file_counts)} channels across {len(files)} log files.\n")
    print(f"{'Channel':<24} {'Files':>5} {'Lines':>10} {'First Seen':>20} {'Last Seen':>20}")
    print("-" * 84)
    for channel, count in sorted(file_counts.items()):
        first, last = span.get(channel, ("-", "-"))
        print(f"{channel:<24} {count:>5} {entry_counts[channel]:>10} {first:>20} {last:>20}")


if __name__ == "__main__":
    main(captures_root(Path(__file__).resolve().parent))
