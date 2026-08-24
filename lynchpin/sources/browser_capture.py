"""Read-only inventory and parsing for browser-capture inboxes.

The adapter accepts an explicit inbox root so operator-specific paths stay in
private configuration.  It inventories every regular file without changing
the capture tree, prioritizes structured state, and reports unsupported or
malformed captures without manufacturing conversation data.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from ..core.parse import parse_datetime

ParseStatus = Literal[
    "parsed",
    "malformed",
    "unsupported_format",
    "unrecognized_structured",
    "skipped_oversized",
]
TimestampPrecision = Literal["second", "millisecond", "microsecond", "unknown"]
HASH_CHUNK_BYTES = 1024 * 1024
DEFAULT_MAX_STRUCTURED_BYTES = 16 * 1024 * 1024
_STRUCTURED_FORMATS = frozenset(("json", "ndjson", "html"))


@dataclass(frozen=True)
class BrowserTimestamp:
    value: datetime | None
    precision: TimestampPrecision


@dataclass(frozen=True)
class BrowserMessage:
    message_id: str | None
    parent_message_id: str | None
    branch_id: str | None
    role: str | None
    content: str | None
    content_sha256: str | None
    created_at: BrowserTimestamp
    edited_at: BrowserTimestamp
    revision_id: str | None
    is_regeneration: bool | None
    model: str | None
    attachment_references: tuple[str, ...]
    source_document_references: tuple[str, ...]


@dataclass(frozen=True)
class BrowserConversation:
    provider: str | None
    conversation_id: str | None
    capture_id: str | None
    canonical_url: str | None
    title: str | None
    created_at: BrowserTimestamp
    updated_at: BrowserTimestamp
    messages: tuple[BrowserMessage, ...]
    deduplication_keys: tuple[str, ...]


@dataclass(frozen=True)
class BrowserCaptureFile:
    root_link: Path
    root_resolved: Path
    link_path: Path
    resolved_path: Path
    size_bytes: int
    capture_mtime: datetime
    content_sha256: str
    mime_type: str
    format: str
    provider: str | None
    conversation_id: str | None
    capture_id: str | None
    parse_status: ParseStatus
    parse_error: str | None
    conversations: tuple[BrowserConversation, ...]
    deduplication_keys: tuple[str, ...]


__all__ = [
    "BrowserCaptureFile",
    "BrowserConversation",
    "BrowserMessage",
    "BrowserTimestamp",
    "DEFAULT_MAX_STRUCTURED_BYTES",
    "HASH_CHUNK_BYTES",
    "ParseStatus",
    "TimestampPrecision",
    "inventory_browser_captures",
    "parse_browser_capture",
]


def inventory_browser_captures(
    inbox_root: Path,
    *,
    max_structured_bytes: int = DEFAULT_MAX_STRUCTURED_BYTES,
) -> tuple[BrowserCaptureFile, ...]:
    """Inventory an inbox recursively without writing to owner-native files.

    ``root_link`` retains the caller-supplied path while ``root_resolved`` and
    each file's ``resolved_path`` retain the resolved target needed for source
    provenance. Missing roots are represented by an empty inventory.
    """
    root_link = inbox_root
    if not root_link.exists() or not root_link.is_dir():
        return ()
    root_resolved = root_link.resolve()
    captures: list[BrowserCaptureFile] = []
    for directory, dirnames, filenames in os.walk(root_link, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink() or not path.is_file():
                continue
            captures.append(
                parse_browser_capture(
                    path,
                    root_link=root_link,
                    root_resolved=root_resolved,
                    max_structured_bytes=max_structured_bytes,
                )
            )
    return tuple(captures)


def parse_browser_capture(
    path: Path,
    *,
    root_link: Path | None = None,
    root_resolved: Path | None = None,
    max_structured_bytes: int = DEFAULT_MAX_STRUCTURED_BYTES,
) -> BrowserCaptureFile:
    """Read one capture and return inventory metadata plus structured records."""
    if max_structured_bytes < 0:
        raise ValueError("max_structured_bytes must not be negative")
    link_path = path
    root_link = root_link or path.parent
    root_resolved = root_resolved or root_link.resolve()
    stat = path.stat()
    content_sha256 = _sha256_file(path)
    format_name, mime_type = _format_for(path)
    conversations: tuple[BrowserConversation, ...] = ()
    parse_status: ParseStatus
    parse_error: str | None = None

    if format_name in _STRUCTURED_FORMATS and stat.st_size > max_structured_bytes:
        parse_status = "skipped_oversized"
        parse_error = f"structured file exceeds maximum parse size of {max_structured_bytes} bytes"
    elif format_name == "json":
        payload = path.read_bytes()
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except UnicodeDecodeError:
            parse_status, parse_error = "malformed", "json is not utf-8"
        except json.JSONDecodeError:
            parse_status, parse_error = "malformed", "invalid json"
        else:
            conversations = _conversations_from_payload(decoded)
            parse_status = "parsed" if conversations else "unrecognized_structured"
    elif format_name == "ndjson":
        payload = path.read_bytes()
        try:
            decoded_rows = _decode_ndjson(payload)
        except UnicodeDecodeError:
            parse_status, parse_error = "malformed", "ndjson is not utf-8"
        except json.JSONDecodeError:
            parse_status, parse_error = "malformed", "invalid ndjson"
        else:
            conversations = _conversations_from_payload(decoded_rows)
            parse_status = "parsed" if conversations else "unrecognized_structured"
    elif format_name == "html":
        payload = path.read_bytes()
        embedded = _embedded_json(payload)
        if embedded is None:
            parse_status, parse_error = "unsupported_format", "no embedded structured state"
        else:
            conversations = _conversations_from_payload(embedded)
            parse_status = "parsed" if conversations else "unrecognized_structured"
    else:
        parse_status, parse_error = "unsupported_format", "format has no structured parser"

    provider = _only_value(conversation.provider for conversation in conversations)
    conversation_id = _only_value(conversation.conversation_id for conversation in conversations)
    capture_id = _only_value(conversation.capture_id for conversation in conversations)
    deduplication_keys = tuple(
        dict.fromkeys(key for conversation in conversations for key in conversation.deduplication_keys)
    )
    return BrowserCaptureFile(
        root_link=root_link,
        root_resolved=root_resolved,
        link_path=link_path,
        resolved_path=path.resolve(),
        size_bytes=stat.st_size,
        capture_mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        content_sha256=content_sha256,
        mime_type=mime_type,
        format=format_name,
        provider=provider,
        conversation_id=conversation_id,
        capture_id=capture_id,
        parse_status=parse_status,
        parse_error=parse_error,
        conversations=conversations,
        deduplication_keys=deduplication_keys,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _format_for(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json", "application/json"
    if suffix in {".jsonl", ".ndjson"}:
        return "ndjson", "application/x-ndjson"
    if suffix in {".html", ".htm"}:
        return "html", "text/html"
    return "unknown", mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _decode_ndjson(payload: bytes) -> list[object]:
    text = payload.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class _EmbeddedJsonParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._is_structured_script = False
        self._chunks: list[str] = []
        self._payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        self._is_structured_script = attributes.get("type") == "application/json" or attributes.get("id") == "__NEXT_DATA__"
        if self._is_structured_script:
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._is_structured_script:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._is_structured_script and self._chunks:
                self._payloads.append("".join(self._chunks))
            self._is_structured_script = False

    @property
    def payloads(self) -> tuple[str, ...]:
        return tuple(self._payloads)


def _embedded_json(payload: bytes) -> object | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parser = _EmbeddedJsonParser()
    parser.feed(text)
    parser.close()
    for candidate in parser.payloads:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _conversations_from_payload(payload: object) -> tuple[BrowserConversation, ...]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = _conversation_candidates(payload)
    else:
        return ()
    conversations = [_conversation_from_item(candidate) for candidate in candidates if isinstance(candidate, dict)]
    return tuple(conversation for conversation in conversations if conversation is not None)


def _conversation_candidates(payload: dict[str, object]) -> list[object]:
    for key in ("conversations", "items", "chats"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, list):
        return data
    return [payload]


def _conversation_from_item(item: dict[str, object]) -> BrowserConversation | None:
    provider = _provider_for(item)
    conversation_id = _first_string(item, "conversation_id", "id", "uuid", "chat_id", "session_id")
    capture_id = _first_string(item, "capture_id", "capture_uuid", "snapshot_id")
    canonical_url = _canonical_url(_first_string(item, "canonical_url", "conversation_url", "share_url", "url"))
    title = _first_string(item, "title", "name")
    created_at = _timestamp(_first_value(item, "create_time", "created_at", "created", "timestamp"))
    updated_at = _timestamp(_first_value(item, "update_time", "updated_at", "updated", "modified_at"))
    messages = _messages_from_item(item)
    if conversation_id is None and canonical_url is None and not messages:
        return None
    if provider is None and "mapping" in item:
        provider = "chatgpt"
    keys = _deduplication_keys(provider, conversation_id, canonical_url, messages)
    return BrowserConversation(
        provider=provider,
        conversation_id=conversation_id,
        capture_id=capture_id,
        canonical_url=canonical_url,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        messages=messages,
        deduplication_keys=keys,
    )


def _messages_from_item(item: dict[str, object]) -> tuple[BrowserMessage, ...]:
    mapping = item.get("mapping")
    if isinstance(mapping, dict):
        messages: list[BrowserMessage] = []
        for node_id, node in mapping.items():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            messages.append(_message_from_item(message, node=node, fallback_id=str(node_id)))
        return tuple(messages)
    for key in ("messages", "chat_messages", "turns"):
        raw_messages = item.get(key)
        if isinstance(raw_messages, list):
            return tuple(_message_from_item(message) for message in raw_messages if isinstance(message, dict))
    return ()


def _message_from_item(
    item: dict[str, object],
    *,
    node: dict[str, object] | None = None,
    fallback_id: str | None = None,
) -> BrowserMessage:
    author = item.get("author")
    role = _first_string(author, "role") if isinstance(author, dict) else None
    role = role or _first_string(item, "role", "sender_role")
    content = _content_text(item.get("content")) or _first_string(item, "text", "message")
    metadata = item.get("metadata")
    model = _first_string(metadata, "model_slug", "model", "model_name") if isinstance(metadata, dict) else None
    revision_id = _first_string(item, "revision_id", "edit_id")
    is_regeneration = _first_bool(item, "is_regeneration", "regenerated")
    if isinstance(metadata, dict):
        revision_id = revision_id or _first_string(metadata, "revision_id", "edit_id")
        is_regeneration = is_regeneration if is_regeneration is not None else _first_bool(metadata, "is_regeneration", "regenerated")
    parent_message_id = _first_string(node, "parent") if node is not None else _first_string(item, "parent_id", "parent_message_id")
    branch_id = _first_string(node, "branch_id") if node is not None else _first_string(item, "branch_id", "branch")
    return BrowserMessage(
        message_id=_first_string(item, "id", "message_id", "uuid") or fallback_id,
        parent_message_id=parent_message_id,
        branch_id=branch_id,
        role=role,
        content=content,
        content_sha256=_content_hash(content),
        created_at=_timestamp(_first_value(item, "create_time", "created_at", "timestamp", "time")),
        edited_at=_timestamp(_first_value(item, "edited_at", "update_time", "updated_at")),
        revision_id=revision_id,
        is_regeneration=is_regeneration,
        model=model,
        attachment_references=_references(item.get("attachments")),
        source_document_references=_references(item.get("source_documents") or item.get("sources")),
    )


def _provider_for(item: dict[str, object]) -> str | None:
    explicit = _first_string(item, "provider", "source_provider", "platform")
    provider = _normalize_provider(explicit)
    if provider is not None:
        return provider
    return _normalize_provider(_first_string(item, "canonical_url", "conversation_url", "share_url", "url"))


def _normalize_provider(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if "chatgpt" in lowered or "openai" in lowered:
        return "chatgpt"
    if "claude" in lowered or "anthropic" in lowered:
        return "claude"
    if "gemini" in lowered or "bard" in lowered or "google" in lowered:
        return "gemini"
    return None


def _deduplication_keys(
    provider: str | None,
    conversation_id: str | None,
    canonical_url: str | None,
    messages: tuple[BrowserMessage, ...],
) -> tuple[str, ...]:
    keys: list[str] = []
    scope = provider or "unknown"
    if conversation_id:
        keys.append(f"conversation:{scope}:{conversation_id}")
    if canonical_url:
        keys.append(f"url:{canonical_url}")
    for message in messages:
        if message.message_id:
            keys.append(f"message:{scope}:{conversation_id or 'unknown'}:{message.message_id}")
        if message.content_sha256:
            keys.append(f"content:{message.content_sha256}")
        if message.parent_message_id and message.message_id:
            keys.append(f"lineage:{scope}:{message.parent_message_id}:{message.message_id}")
    return tuple(dict.fromkeys(keys))


def _timestamp(value: object) -> BrowserTimestamp:
    if value is None or value == "":
        return BrowserTimestamp(None, "unknown")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if abs(seconds) >= 100_000_000_000:
            seconds /= 1_000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return BrowserTimestamp(None, "unknown")
        return BrowserTimestamp(parsed, _numeric_precision(value))
    if isinstance(value, str):
        return BrowserTimestamp(parse_datetime(value), _string_precision(value))
    return BrowserTimestamp(None, "unknown")


def _numeric_precision(value: int | float) -> TimestampPrecision:
    if isinstance(value, int):
        return "second"
    fraction = str(value).partition(".")[2].rstrip("0")
    if not fraction:
        return "second"
    return "millisecond" if len(fraction) <= 3 else "microsecond"


def _string_precision(value: str) -> TimestampPrecision:
    match = re.search(r"\.([0-9]+)(?:Z|[+-][0-9:]+)?$", value.strip())
    if match is None:
        return "second" if parse_datetime(value) is not None else "unknown"
    return "millisecond" if len(match.group(1)) <= 3 else "microsecond"


def _canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    parts = value.get("parts")
    if not isinstance(parts, list):
        return _first_string(value, "text")
    text_parts = [part for part in parts if isinstance(part, str)]
    return "\n".join(text_parts) if text_parts else None


def _content_hash(content: str | None) -> str | None:
    if content is None:
        return None
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


def _references(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    references: list[str] = []
    for item in value:
        if isinstance(item, str):
            references.append(item)
        elif isinstance(item, dict):
            reference = _first_string(item, "id", "url", "name", "file_id", "document_id")
            if reference:
                references.append(reference)
    return tuple(dict.fromkeys(references))


def _first_string(value: object, *keys: str) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _first_value(value: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None:
            return candidate
    return None


def _first_bool(value: object, *keys: str) -> bool | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, bool):
            return candidate
    return None


def _only_value(values: Any) -> str | None:
    found = tuple(dict.fromkeys(value for value in values if value))
    return found[0] if len(found) == 1 else None
