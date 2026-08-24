#!/usr/bin/env python3
"""Extract conversational windows involving sinity from WeeChat logs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Iterable, List, Optional, Set

from irc_log_utils import LogEntry, captures_root, iter_entries  # type: ignore

ROOT = captures_root(Path(__file__).resolve().parent)
PROCESSED_DIR = ROOT / "_processed"
OUTPUT_DIR = PROCESSED_DIR / "sinity"

TARGET = "sinity"
BUFFER_WINDOW = timedelta(minutes=30)
BUFFER_MAX_LINES = 150
GAP = timedelta(hours=4)
ANCHOR_GAP = timedelta(hours=2)
SKIP_ACTORS = {"-->", "<--", "--", "***"}


@dataclass
class Conversation:
    channel: str
    lines: List[str] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    last_anchor: Optional[datetime] = None
    sinity_lines: int = 0
    mention_lines: int = 0

    def add_entry(self, entry: LogEntry, *, anchor: bool = False) -> None:
        if self.start is None:
            self.start = entry.timestamp
        self.end = entry.timestamp
        self.last_timestamp = entry.timestamp
        if anchor:
            self.last_anchor = entry.timestamp
        source = entry.source_path
        if source is not None:
            try:
                rel = source.relative_to(ROOT)
            except ValueError:
                rel = source
            self.sources.add(str(rel))
        nick = entry.nick.strip().lower()
        if nick.startswith(TARGET):
            self.sinity_lines += 1
        elif TARGET in entry.message.lower():
            self.mention_lines += 1
        self.lines.append(entry.raw.rstrip("\n"))


def actor_name(entry: LogEntry) -> str:
    return entry.nick.strip()


def is_anchor(entry: LogEntry) -> bool:
    actor = actor_name(entry)
    if actor in SKIP_ACTORS:
        return False
    text = entry.message.lower()
    if actor.lower().startswith(TARGET):
        return True
    return TARGET in text


def trim_buffer(buffer: Deque[LogEntry], current_time: datetime) -> None:
    while buffer and current_time - buffer[0].timestamp > BUFFER_WINDOW:
        buffer.popleft()
    while len(buffer) > BUFFER_MAX_LINES:
        buffer.popleft()


def iter_concat_logs(root: Path) -> Iterable[Path]:
    return sorted((root / "_processed").glob("concat-*.log"))


def display_channel(log_file: Path) -> str:
    stem = log_file.stem
    if stem.startswith("concat-"):
        base = stem[len("concat-"):]
        if base.startswith("#"):
            return base
        return f"#{base}" if base not in {"libera"} else base
    return stem


def collect_conversations(root: Path) -> List[Conversation]:
    conversations: List[Conversation] = []
    for log_file in iter_concat_logs(root):
        channel_name = display_channel(log_file)
        buffer: Deque[LogEntry] = deque()
        current: Optional[Conversation] = None
        for entry in iter_entries([log_file]):
            entry.channel = channel_name
            current_time = entry.timestamp

            if current and current.last_anchor is not None:
                if current_time - current.last_anchor > ANCHOR_GAP:
                    conversations.append(current)
                    current = None

            if current and current.last_timestamp is not None:
                if current_time - current.last_timestamp > GAP:
                    conversations.append(current)
                    current = None

            anchor = is_anchor(entry)

            if current is None:
                buffer.append(entry)
                trim_buffer(buffer, current_time)
            else:
                current.add_entry(entry, anchor=anchor)

            if anchor and current is None:
                current = Conversation(channel=channel_name)
                for buffered in buffer:
                    buffered_anchor = is_anchor(buffered)
                    current.add_entry(buffered, anchor=buffered_anchor)
                buffer.clear()

        if current:
            conversations.append(current)

    return conversations


def sanitise(text: str) -> str:
    cleaned = text.lstrip("#")
    allowed = []
    for ch in cleaned:
        if ch.isalnum() or ch in ("-", "_"):
            allowed.append(ch)
        elif ch in {" ", "/"}:
            allowed.append("-")
    return "".join(allowed) or "channel"


def format_ts(dt: Optional[datetime]) -> str:
    if dt is None:
        return "unknown"
    return dt.strftime("%Y%m%dT%H%M%S")


def write_conversations(conversations: Iterable[Conversation], output_dir: Path) -> int:
    output_dir.mkdir(exist_ok=True)
    # Clear previous run
    for existing in output_dir.glob("*.log"):
        existing.unlink()
    index_path = output_dir / "index.txt"
    if index_path.exists():
        index_path.unlink()

    written = 0
    index_lines: List[str] = []
    for idx, convo in enumerate(conversations, start=1):
        if convo.sinity_lines == 0:
            continue
        start_str = format_ts(convo.start)
        end_str = format_ts(convo.end)
        channel_label = sanitise(convo.channel)
        total = len(convo.lines)
        filename = f"{idx:04d}_{channel_label}_{start_str}_{end_str}_s{convo.sinity_lines}m{convo.mention_lines}t{total}.log"
        path = output_dir / filename
        header = f"=== Conversation {idx} | {convo.channel} | {start_str} -> {end_str} | sources: {', '.join(sorted(convo.sources)) if convo.sources else 'unknown'} | sinity_lines: {convo.sinity_lines} | mentions: {convo.mention_lines} | total: {total}"
        with path.open("w", encoding="utf-8") as handle:
            handle.write(header + "\n")
            for line in convo.lines:
                handle.write(line + "\n")
        index_lines.append(header + f" | file: {filename}")
        written += 1

    if index_lines:
        index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return written


def main() -> None:
    conversations = collect_conversations(ROOT)
    count = write_conversations(conversations, OUTPUT_DIR)
    print(f"Wrote {count} conversations to {OUTPUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
