#!/usr/bin/env python3
"""Build recent #lesswrong context files.

This creates a small bundle analogous to ``sinity_conversations.log`` while
also preserving the recent full-channel ``concat-lesswrong`` slice that the
conversation extraction came from.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Deque, Iterable, List, Optional, Sequence, Set

from irc_log_utils import LogEntry, captures_root, file_date, iter_entries


ROOT = captures_root(Path(__file__).resolve().parent)
DEFAULT_CHANNEL = "#lesswrong"
DEFAULT_TARGET = "sinity"
DEFAULT_DAYS = 30
BUFFER_WINDOW = timedelta(minutes=30)
BUFFER_MAX_LINES = 150
GAP = timedelta(hours=4)
ANCHOR_GAP = timedelta(hours=2)
SKIP_ACTORS = {"-->", "<--", "--", "***"}


@dataclass
class Conversation:
    channel: str
    target: str
    lines: List[str] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    last_anchor: Optional[datetime] = None
    target_lines: int = 0
    mention_lines: int = 0

    def add_entry(self, entry: LogEntry, *, anchor: bool = False) -> None:
        if self.start is None:
            self.start = entry.timestamp
        self.end = entry.timestamp
        self.last_timestamp = entry.timestamp
        if anchor:
            self.last_anchor = entry.timestamp

        if entry.source_path is not None:
            try:
                self.sources.add(str(entry.source_path.relative_to(ROOT)))
            except ValueError:
                self.sources.add(str(entry.source_path))

        actor = entry.nick.strip().lower()
        target = self.target.lower()
        if actor.startswith(target):
            self.target_lines += 1
        elif target in entry.message.lower():
            self.mention_lines += 1

        self.lines.append(entry.raw.rstrip("\n"))


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def channel_label(channel: str) -> str:
    return channel.lstrip("#")


def channel_dir(root: Path, channel: str) -> Path:
    raw = root / "_raw"
    labelled = raw / channel
    if labelled.is_dir():
        return labelled
    stripped = raw / channel_label(channel)
    if stripped.is_dir():
        return stripped
    raise FileNotFoundError(f"no raw log directory for {channel!r} under {raw}")


def iter_recent_logs(
    root: Path,
    channel: str,
    *,
    since: date,
    until: date,
    include_live: bool,
) -> List[Path]:
    paths: List[Path] = []
    today = date.today()
    for path in sorted(channel_dir(root, channel).glob("*.log")):
        day = file_date(path)
        if day is None or day < since or day > until:
            continue
        if not include_live and day >= today:
            continue
        paths.append(path)
    return paths


def is_anchor(entry: LogEntry, target: str) -> bool:
    actor = entry.nick.strip()
    if actor in SKIP_ACTORS:
        return False
    target_lower = target.lower()
    if actor.lower().startswith(target_lower):
        return True
    return target_lower in entry.message.lower()


def trim_buffer(buffer: Deque[LogEntry], current_time: datetime) -> None:
    while buffer and current_time - buffer[0].timestamp > BUFFER_WINDOW:
        buffer.popleft()
    while len(buffer) > BUFFER_MAX_LINES:
        buffer.popleft()


def collect_conversations(paths: Sequence[Path], *, channel: str, target: str) -> List[Conversation]:
    conversations: List[Conversation] = []
    buffer: Deque[LogEntry] = deque()
    current: Optional[Conversation] = None

    for entry in iter_entries(paths):
        entry.channel = channel
        current_time = entry.timestamp

        if current and current.last_anchor is not None:
            if current_time - current.last_anchor > ANCHOR_GAP:
                conversations.append(current)
                current = None

        if current and current.last_timestamp is not None:
            if current_time - current.last_timestamp > GAP:
                conversations.append(current)
                current = None

        anchor = is_anchor(entry, target)

        if current is None:
            buffer.append(entry)
            trim_buffer(buffer, current_time)
        else:
            current.add_entry(entry, anchor=anchor)

        if anchor and current is None:
            current = Conversation(channel=channel, target=target)
            for buffered in buffer:
                current.add_entry(buffered, anchor=is_anchor(buffered, target))
            buffer.clear()

    if current:
        conversations.append(current)

    return [convo for convo in conversations if convo.target_lines > 0]


def format_compact(dt: Optional[datetime]) -> str:
    if dt is None:
        return "unknown"
    return dt.strftime("%Y%m%dT%H%M%S")


def format_human(dt: Optional[datetime]) -> str:
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def write_recent_concat(paths: Sequence[Path], output_path: Path) -> int:
    line_count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out_handle:
        for path in paths:
            with path.open("r", encoding="utf-8", errors="replace") as in_handle:
                for line in in_handle:
                    out_handle.write(line)
                    line_count += 1
    return line_count


def write_conversation_files(
    conversations: Sequence[Conversation],
    *,
    output_path: Path,
    index_path: Path,
    concat_source: Path,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    index_lines: List[str] = []

    with output_path.open("w", encoding="utf-8") as handle:
        for idx, convo in enumerate(conversations, start=1):
            total = len(convo.lines)
            header = (
                f"=== Conversation {idx} | {convo.channel} | "
                f"{format_human(convo.start)} -> {format_human(convo.end)} | "
                f"sources: {concat_source.name} | "
                f"{convo.target}_lines: {convo.target_lines} | "
                f"mentions: {convo.mention_lines} | total: {total}"
            )
            if idx > 1:
                handle.write("\n")
            handle.write(header + "\n")
            for line in convo.lines:
                handle.write(line + "\n")

            index_lines.append(
                f"=== Conversation {idx} | {convo.channel} | "
                f"{format_compact(convo.start)} -> {format_compact(convo.end)} | "
                f"sources: {concat_source.name} | "
                f"{convo.target}_lines: {convo.target_lines} | "
                f"mentions: {convo.mention_lines} | total: {total}"
            )

    index_path.write_text("\n".join(index_lines) + ("\n" if index_lines else ""), encoding="utf-8")
    return sum(len(convo.lines) for convo in conversations)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--since", type=parse_day)
    parser.add_argument("--until", type=parse_day, default=date.today())
    parser.add_argument(
        "--exclude-live",
        action="store_true",
        help="skip today's still-open log file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "_processed" / "recent",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    since = args.since
    if since is None:
        since = args.until - timedelta(days=args.days)

    if args.until < since:
        raise SystemExit(f"--until {args.until} is before --since {since}")

    label = channel_label(args.channel)
    output_dir = args.output_dir
    concat_path = output_dir / f"concat-{label}-recent.log"
    conversations_path = output_dir / f"{args.target}-{label}-recent-conversations.log"
    index_path = output_dir / f"{args.target}-{label}-recent-index.txt"

    paths = iter_recent_logs(
        ROOT,
        args.channel,
        since=since,
        until=args.until,
        include_live=not args.exclude_live,
    )
    if not paths:
        raise SystemExit(f"no logs found for {args.channel} from {since} to {args.until}")

    concat_lines = write_recent_concat(paths, concat_path)
    conversations = collect_conversations(paths, channel=args.channel, target=args.target)
    conversation_lines = write_conversation_files(
        conversations,
        output_path=conversations_path,
        index_path=index_path,
        concat_source=concat_path,
    )

    print(f"range: {since} -> {args.until}")
    print(f"sources: {len(paths)} files")
    print(f"wrote: {concat_path.relative_to(ROOT)} ({concat_lines} lines)")
    print(
        f"wrote: {conversations_path.relative_to(ROOT)} "
        f"({len(conversations)} conversations, {conversation_lines} lines)"
    )
    print(f"wrote: {index_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
