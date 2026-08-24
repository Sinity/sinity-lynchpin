#!/usr/bin/env python3
"""Concatenate daily WeeChat channel logs into per-channel files.

Reads per-day logs from ``<root>/_raw/<channel>/`` (the post-2026-04-08
layout) and writes one ``concat-<channel>.log`` per channel into
``<root>/_processed/``. The current day (and any future-dated stragglers)
are skipped because weechat still holds an fd open and may flush more
lines at any moment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

from irc_log_utils import captures_root, channel_from_path, file_date, iter_log_files


def iter_source_logs(root: Path) -> Iterable[Path]:
    today = date.today()
    for log_file in iter_log_files(root):
        log_day = file_date(log_file)
        if log_day is not None and log_day >= today:
            print(f"Skipping live file: {log_file.relative_to(root)} (date {log_day})")
            continue
        yield log_file


def concatenate_logs(root: Path) -> None:
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for log_file in iter_source_logs(root):
        channel = channel_from_path(log_file)
        if channel is None:
            continue
        # Strip the leading ``#`` for output filenames so concat-lesswrong.log
        # stays consistent with the previous pipeline naming.
        grouped[channel.lstrip("#")].append(log_file)

    if not grouped:
        print("No channel logs found.")
        return

    output_dir = root / "_processed"
    output_dir.mkdir(exist_ok=True)
    for channel, files in sorted(grouped.items()):
        target = output_dir / f"concat-{channel}.log"
        with target.open("w", encoding="utf-8") as out_handle:
            for file_path in files:
                with file_path.open("r", encoding="utf-8", errors="replace") as in_handle:
                    for line in in_handle:
                        out_handle.write(line)
        print(f"Wrote {target.relative_to(root)} from {len(files)} files")


def main() -> None:
    root = captures_root(Path(__file__).resolve().parent)
    concatenate_logs(root)


if __name__ == "__main__":
    main()
