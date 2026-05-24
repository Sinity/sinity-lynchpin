"""IRC log source for prompt-facing retrospective evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.parse import as_local

__all__ = [
    "IRCMessage",
    "IRCConversation",
    "conversation_files",
    "conversations",
    "conversations_in_range",
]


_HEADER_RE = re.compile(
    r"^=== Conversation (?P<id>\d+) \| (?P<channel>#[^|]+) \| "
    r"(?P<start>\d{4}-?\d{2}-?\d{2}[ T]\d{2}:?\d{2}:?\d{2}) -> "
    r"(?P<end>\d{4}-?\d{2}-?\d{2}[ T]\d{2}:?\d{2}:?\d{2})"
)
_LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\t(?P<speaker>[^\t]+)\t(?P<text>.*)$")


@dataclass(frozen=True)
class IRCMessage:
    timestamp: datetime
    speaker: str
    text: str


@dataclass(frozen=True)
class IRCConversation:
    conversation_id: str
    channel: str
    start: datetime
    end: datetime
    source_path: str
    sinity_lines: int
    mention_lines: int
    total_lines: int
    messages: tuple[IRCMessage, ...]

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class _ConversationHeader:
    conversation_id: str
    channel: str
    start: datetime
    end: datetime


def _parse_stamp(value: str) -> datetime | None:
    normalized = value.replace("T", " ")
    if "-" not in normalized[:10] and len(normalized) >= 15:
        normalized = (
            f"{normalized[0:4]}-{normalized[4:6]}-{normalized[6:8]} "
            f"{normalized[9:11]}:{normalized[11:13]}:{normalized[13:15]}"
        )
    try:
        return as_local(datetime.fromisoformat(normalized))
    except ValueError:
        return None


def conversation_files(*, root: Optional[Path] = None) -> list[Path]:
    base = root or get_config().irc_root
    processed = base / "_processed" / "sinity"
    if processed.exists():
        return sorted(processed.glob("*.log"))
    single_file_export = base / "sinity_conversations.log"
    return [single_file_export] if single_file_export.exists() else []


def conversations(*, root: Optional[Path] = None) -> Iterator[IRCConversation]:
    for path in conversation_files(root=root):
        yield from _parse_file(path)


def conversations_in_range(*, start: date, end: date, root: Optional[Path] = None) -> list[IRCConversation]:
    return [
        conv
        for conv in conversations(root=root)
        if conv.start.date() <= end and conv.end.date() >= start
    ]


def _parse_file(path: Path) -> Iterator[IRCConversation]:
    current: _ConversationHeader | None = None
    messages: list[IRCMessage] = []

    def flush() -> IRCConversation | None:
        nonlocal current, messages
        if current is None:
            return None
        conv = IRCConversation(
            conversation_id=current.conversation_id,
            channel=current.channel.strip(),
            start=current.start,
            end=current.end,
            source_path=str(path),
            sinity_lines=sum(1 for msg in messages if msg.speaker.lower() == "sinity"),
            mention_lines=sum(1 for msg in messages if "sinity" in msg.text.lower()),
            total_lines=len(messages),
            messages=tuple(messages),
        )
        current = None
        messages = []
        return conv

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            header = _HEADER_RE.match(line)
            if header:
                existing = flush()
                if existing is not None:
                    yield existing
                start = _parse_stamp(header.group("start"))
                end = _parse_stamp(header.group("end"))
                if start is None or end is None:
                    continue
                current = _ConversationHeader(
                    conversation_id=header.group("id"),
                    channel=header.group("channel"),
                    start=start,
                    end=end,
                )
                continue
            if current is None:
                continue
            match = _LINE_RE.match(line)
            if not match:
                continue
            stamp = _parse_stamp(match.group("ts"))
            if stamp is None:
                continue
            messages.append(IRCMessage(
                timestamp=stamp,
                speaker=match.group("speaker"),
                text=match.group("text"),
            ))
    existing = flush()
    if existing is not None:
        yield existing
