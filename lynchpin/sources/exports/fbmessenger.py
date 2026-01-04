from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config


@dataclass(frozen=True)
class MessengerThread:
    thread_name: str
    participants: List[str]
    source: str


@dataclass(frozen=True)
class MessengerMessage:
    thread_name: str
    participants: List[str]
    sender: str
    timestamp: Optional[datetime]
    text: Optional[str]
    kind: str
    is_unsent: bool
    media_count: int
    reaction_count: int
    source: str


def _resolve_export_dir(root: Path) -> Optional[Path]:
    if root.is_dir() and (root / "messages").exists():
        return root
    if not root.exists():
        return None
    subdirs = [child for child in root.iterdir() if child.is_dir() and child.name not in {"raw", "archive"}]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    fallback: list[Path] = []
    for path in subdirs:
        try:
            parsed = datetime.strptime(path.name, "%Y-%m-%d")
        except ValueError:
            fallback.append(path)
            continue
        dated.append((parsed, path))
    if dated:
        dated.sort(key=lambda item: item[0], reverse=True)
        return dated[0][1]
    fallback.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return fallback[0]


def _thread_files(paths: Optional[Sequence[Path]]) -> List[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).is_file()]
    cfg = get_config()
    export_dir = _resolve_export_dir(cfg.fbmessenger_gdpr_root)
    if not export_dir:
        return []
    messages_dir = export_dir / "messages"
    if not messages_dir.exists():
        return []
    return sorted(messages_dir.glob("*.json"))


def _thread_signature(paths: Optional[Sequence[Path]]) -> Tuple[Tuple[str, ...], str]:
    resolved = _thread_files(paths)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache("fbmessenger_threads", depends_on=lambda paths=None: _thread_signature(paths))
def _load_threads(paths: Optional[Sequence[Path]]) -> List[MessengerThread]:
    threads: List[MessengerThread] = []
    for path in _thread_files(paths):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        participants = [_clean_text(p) for p in list(data.get("participants", []) or [])]
        thread_name = _clean_text(data.get("threadName") or path.stem)
        threads.append(
            MessengerThread(
                thread_name=thread_name,
                participants=participants,
                source=_clean_path(path),
            )
        )
    return threads


def iter_threads(paths: Optional[Sequence[Path]] = None) -> Iterator[MessengerThread]:
    yield from _load_threads(paths)


@persistent_cache("fbmessenger_messages", depends_on=lambda paths=None: _thread_signature(paths))
def _load_messages(paths: Optional[Sequence[Path]]) -> List[MessengerMessage]:
    messages: List[MessengerMessage] = []
    for path in _thread_files(paths):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        participants = [_clean_text(p) for p in list(data.get("participants", []) or [])]
        thread_name = _clean_text(data.get("threadName") or path.stem)
        for message in data.get("messages", []) or []:
            ts_raw = message.get("timestamp")
            timestamp = None
            if isinstance(ts_raw, (int, float)):
                timestamp = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
            messages.append(
                MessengerMessage(
                    thread_name=thread_name,
                    participants=participants,
                    sender=_clean_text(message.get("senderName") or ""),
                    timestamp=timestamp,
                    text=_clean_text(message.get("text")),
                    kind=_clean_text(message.get("type") or ""),
                    is_unsent=bool(message.get("isUnsent")),
                    media_count=len(message.get("media") or []),
                    reaction_count=len(message.get("reactions") or []),
                    source=_clean_path(path),
                )
            )
    return messages


def iter_messages(paths: Optional[Sequence[Path]] = None) -> Iterator[MessengerMessage]:
    yield from _load_messages(paths)


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value.encode("utf-8", "replace").decode("utf-8")


def _clean_path(path: Path) -> str:
    text = str(path)
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
