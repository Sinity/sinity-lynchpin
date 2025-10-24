"""Facebook Messenger GDPR export reader."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config
from ..core.primitives import logical_date

__all__ = [
    "MessengerThread",
    "MessengerMessage",
    "MessengerDayActivity",
    "is_operator_sender",
    "iter_fbmessenger_threads",
    "iter_fbmessenger_messages",
    "daily_messenger_activity",
]


def _load_operator_fb_names() -> frozenset[str]:
    """Operator's Facebook display name(s), from optional external config --
    same pattern as outlook.py's operator identity. Falls back to a generic
    placeholder if the config file is absent."""
    path = get_config().derived_root / "local-config" / "operator_identity.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            names = raw.get("messenger_display_names")
            if names:
                return frozenset(str(n) for n in names)
    except (OSError, json.JSONDecodeError):
        pass
    return frozenset({"Operator"})


# The export does not carry a from-me flag, so direction-sensitive views need
# explicit local aliases rather than an all-history sender-frequency heuristic.
_OPERATOR_FB_NAMES: frozenset[str] = _load_operator_fb_names()


@dataclass(frozen=True)
class MessengerThread:
    thread_name: str
    participants: list[str]
    source: str


@dataclass(frozen=True)
class MessengerMessage:
    thread_name: str
    participants: list[str]
    sender: str
    timestamp: Optional[datetime]
    text: Optional[str]
    kind: str
    is_unsent: bool
    media_count: int
    reaction_count: int
    source: str


@dataclass(frozen=True)
class MessengerDayActivity:
    date: date
    message_count: int
    thread_count: int
    sent_count: int


def is_operator_sender(sender: str) -> bool:
    """Return whether a Messenger sender display name belongs to the operator."""
    return sender in _OPERATOR_FB_NAMES


def iter_fbmessenger_threads(
    paths: Optional[list[Path]] = None, *, ensure: bool = True
) -> Iterator[MessengerThread]:
    if ensure and paths is None:
        from ..materialization import ensure_materialized

        ensure_materialized("facebook_messenger")
    yield from _load_threads(paths=paths)


def iter_fbmessenger_messages(
    paths: Optional[list[Path]] = None,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    ensure: bool = True,
) -> Iterator[MessengerMessage]:
    """Iterate Messenger messages, optionally bounded by half-open logical dates."""
    if ensure and paths is None:
        from ..materialization import ensure_materialized

        ensure_materialized("facebook_messenger", window=(start, end) if start and end else None)
    for message in _load_messages(paths=paths):
        if message.timestamp is not None and (start is not None or end is not None):
            d = logical_date(message.timestamp)
            if start is not None and d < start:
                continue
            if end is not None and d >= end:
                continue
        yield message


def daily_messenger_activity(*, start: date, end: date, ensure: bool = True) -> list[MessengerDayActivity]:
    """Daily messenger message counts."""

    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("facebook_messenger", window=(start, end))

    day_messages: dict[date, list[MessengerMessage]] = defaultdict(list)
    for msg in iter_fbmessenger_messages(start=start, end=end, ensure=False):
        if msg.timestamp is None:
            continue
        d = logical_date(msg.timestamp)
        # NOTE: original predicate was exclusive on end (d >= end → skip).
        # Semantics differ from in_date_range (inclusive both ends) so the
        # hand-rolled check is preserved as-is to avoid a behaviour change.
        if d < start or d >= end:
            continue
        day_messages[d].append(msg)

    return sorted(
        [
            MessengerDayActivity(
                date=d,
                message_count=len(msgs),
                thread_count=len({m.thread_name for m in msgs}),
                sent_count=sum(1 for m in msgs if is_operator_sender(m.sender)),
            )
            for d, msgs in day_messages.items()
        ],
        key=lambda x: x.date,
    )


def _thread_files(paths: Optional[list[Path]], *, ensure: bool = True) -> list[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).is_file()]
    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("facebook_messenger")
    cfg = get_config()
    canonical = cfg.exports_root / "comms/facebook-messenger/processed/canonical"
    canonical_threads = canonical / "threads.ndjson"
    if canonical_threads.exists():
        return [canonical_threads]
    raise FileNotFoundError(
        f"canonical Messenger materialization is missing: {canonical_threads}. "
        "Run python -m lynchpin.ingest.exports_materialize facebook-messenger."
    )


def _message_files(paths: Optional[list[Path]], *, ensure: bool = True) -> list[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).is_file()]
    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("facebook_messenger")
    cfg = get_config()
    canonical = cfg.exports_root / "comms/facebook-messenger/processed/canonical"
    canonical_messages = canonical / "messages.ndjson"
    if canonical_messages.exists():
        return [canonical_messages]
    raise FileNotFoundError(
        f"canonical Messenger materialization is missing: {canonical_messages}. "
        "Run python -m lynchpin.ingest.exports_materialize facebook-messenger."
    )


def _thread_signature(paths: Optional[list[Path]] = None) -> object:
    resolved = _thread_files(paths, ensure=False)
    return tuple(str(path) for path in resolved), files_signature(resolved)


def _message_signature(paths: Optional[list[Path]] = None) -> object:
    resolved = _message_files(paths, ensure=False)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache("fbmessenger_threads", depends_on=_thread_signature)
def _load_threads(paths: Optional[list[Path]] = None) -> list[MessengerThread]:
    threads: list[MessengerThread] = []
    for path in _thread_files(paths, ensure=False):
        if path.suffix == ".ndjson":
            threads.extend(_read_canonical_threads(path))
            continue
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            continue
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


@persistent_cache("fbmessenger_messages", depends_on=_message_signature)
def _load_messages(paths: Optional[list[Path]] = None) -> list[MessengerMessage]:
    messages: list[MessengerMessage] = []
    for path in _message_files(paths, ensure=False):
        if path.suffix == ".ndjson":
            messages.extend(_read_canonical_messages(path))
            continue
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            continue
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


def _read_canonical_threads(path: Path) -> list[MessengerThread]:
    rows: list[MessengerThread] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            participants = payload.get("participants") or []
            rows.append(
                MessengerThread(
                    thread_name=str(payload.get("thread_name") or ""),
                    participants=[str(item) for item in participants],
                    source=str(payload.get("source") or path),
                )
            )
    return rows


def _read_canonical_messages(path: Path) -> list[MessengerMessage]:
    rows: list[MessengerMessage] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            raw_timestamp = payload.get("timestamp")
            timestamp = None
            if isinstance(raw_timestamp, str) and raw_timestamp:
                timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            participants = payload.get("participants") or []
            rows.append(
                MessengerMessage(
                    thread_name=str(payload.get("thread_name") or ""),
                    participants=[str(item) for item in participants],
                    sender=str(payload.get("sender") or ""),
                    timestamp=timestamp,
                    text=_clean_text(payload.get("text")),
                    kind=str(payload.get("kind") or ""),
                    is_unsent=bool(payload.get("is_unsent")),
                    media_count=int(payload.get("media_count") or 0),
                    reaction_count=int(payload.get("reaction_count") or 0),
                    source=str(payload.get("source") or path),
                )
            )
    return rows


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value.encode("utf-8", "replace").decode("utf-8")


def _clean_path(path: Path) -> str:
    text = str(path)
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
