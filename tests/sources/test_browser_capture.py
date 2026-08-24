from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lynchpin.sources.browser_capture import HASH_CHUNK_BYTES, inventory_browser_captures, parse_browser_capture


FIXTURE = Path(__file__).parent / "fixtures/browser_capture/structured_export.json"


def test_inventory_records_link_resolution_fingerprint_and_structured_state(tmp_path: Path) -> None:
    capture_root = tmp_path / "capture-target"
    capture_root.mkdir()
    capture_path = capture_root / "export.json"
    payload = FIXTURE.read_bytes()
    capture_path.write_bytes(payload)
    inbox_link = tmp_path / "inbox-link"
    inbox_link.symlink_to(capture_root, target_is_directory=True)

    captures = inventory_browser_captures(inbox_link)

    assert len(captures) == 1
    capture = captures[0]
    assert capture.root_link == inbox_link
    assert capture.root_resolved == capture_root.resolve()
    assert capture.link_path == inbox_link / "export.json"
    assert capture.resolved_path == capture_path.resolve()
    assert capture.size_bytes == len(payload)
    assert capture.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert capture.fingerprint_status == "hashed"
    assert capture.capture_mtime.tzinfo is not None
    assert capture.mime_type == "application/json"
    assert capture.format == "json"
    assert capture.parse_status == "parsed"
    assert capture.provider == "chatgpt"
    assert capture.conversation_id == "demo-conversation"

    conversation = capture.conversations[0]
    assert conversation.canonical_url == "https://chatgpt.example/c/demo-conversation"
    assert [message.message_id for message in conversation.messages] == ["user-message", "assistant-message"]
    assert conversation.messages[0].parent_message_id == "root-node"
    assert conversation.messages[0].created_at.precision == "millisecond"
    assert conversation.messages[1].model == "synthetic-model"
    assert conversation.messages[1].revision_id == "synthetic-revision"
    assert conversation.messages[1].is_regeneration is False
    assert conversation.messages[1].attachment_references == ("synthetic-attachment",)
    assert conversation.messages[1].source_document_references == ("synthetic-document",)
    assert "conversation:chatgpt:demo-conversation" in capture.deduplication_keys
    assert any(key.startswith("content:") for key in capture.deduplication_keys)
    assert "lineage:chatgpt:user-node:assistant-message" in capture.deduplication_keys


def test_html_embedded_structured_state_uses_the_json_parser(tmp_path: Path) -> None:
    conversation = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    html = "<html><script id=\"__NEXT_DATA__\" type=\"application/json\">" + json.dumps(conversation) + "</script></html>"
    capture_path = tmp_path / "capture.html"
    capture_path.write_text(html, encoding="utf-8")

    capture = parse_browser_capture(capture_path)

    assert capture.format == "html"
    assert capture.parse_status == "parsed"
    assert capture.conversations[0].provider == "chatgpt"
    assert len(capture.conversations[0].messages) == 2


def test_malformed_and_unsupported_captures_are_reported_without_fabricated_records(tmp_path: Path) -> None:
    malformed = tmp_path / "broken.json"
    malformed.write_text("{", encoding="utf-8")
    unsupported = tmp_path / "capture.bin"
    unsupported.write_bytes(b"not structured")

    malformed_capture = parse_browser_capture(malformed)
    unsupported_capture = parse_browser_capture(unsupported)

    assert malformed_capture.parse_status == "malformed"
    assert malformed_capture.parse_error == "invalid json"
    assert malformed_capture.conversations == ()
    assert unsupported_capture.parse_status == "unsupported_format"
    assert unsupported_capture.parse_error == "format has no structured parser"
    assert unsupported_capture.conversations == ()


def test_generic_json_ids_are_not_fabricated_as_conversations(tmp_path: Path) -> None:
    capture_path = tmp_path / "records.json"
    capture_path.write_text(
        json.dumps([{"id": "record-1", "title": "Ordinary record"}]),
        encoding="utf-8",
    )

    capture = parse_browser_capture(capture_path)

    assert capture.parse_status == "unrecognized_structured"
    assert capture.conversations == ()


def test_oversized_structured_capture_hashes_in_chunks_without_reading_payload(
    tmp_path: Path, monkeypatch
) -> None:
    capture_path = tmp_path / "oversized.json"
    capture_size = (HASH_CHUNK_BYTES * 3) + 17
    with capture_path.open("wb") as handle:
        handle.truncate(capture_size)

    original_open = Path.open
    reads: list[tuple[int, int]] = []

    class RecordingReader:
        def __init__(self, handle) -> None:
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size: int = -1) -> bytes:
            chunk = self.handle.read(size)
            reads.append((size, len(chunk)))
            return chunk

    def tracked_open(path: Path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == capture_path and args == ("rb",):
            return RecordingReader(handle)
        return handle

    def reject_read_bytes(path: Path) -> bytes:
        raise AssertionError(f"unexpected whole-file read: {path}")

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

    capture = parse_browser_capture(capture_path, max_structured_bytes=HASH_CHUNK_BYTES)

    expected_hash = hashlib.sha256()
    remaining = capture_size
    while remaining:
        chunk_size = min(remaining, HASH_CHUNK_BYTES)
        expected_hash.update(b"\0" * chunk_size)
        remaining -= chunk_size

    assert capture.size_bytes == capture_size
    assert capture.content_sha256 == expected_hash.hexdigest()
    assert capture.parse_status == "skipped_oversized"
    assert capture.parse_error == f"structured file exceeds maximum parse size of {HASH_CHUNK_BYTES} bytes"
    assert capture.conversations == ()
    assert sum(length for _, length in reads) == capture_size
    assert all(size == HASH_CHUNK_BYTES for size, _ in reads)
    assert max(length for _, length in reads) == HASH_CHUNK_BYTES


def test_inventory_does_not_read_large_unsupported_files_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    unsupported = tmp_path / "recording.bin"
    with unsupported.open("wb") as handle:
        handle.truncate(HASH_CHUNK_BYTES * 8)

    original_open = Path.open

    def reject_content_read(path: Path, *args, **kwargs):
        if path == unsupported and args == ("rb",):
            raise AssertionError("unsupported inventory file was read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_content_read)

    capture = inventory_browser_captures(tmp_path)[0]

    assert capture.parse_status == "unsupported_format"
    assert capture.content_sha256 is None
    assert capture.fingerprint_status == "not_requested"
    assert capture.size_bytes == HASH_CHUNK_BYTES * 8


def test_ai_studio_capture_preserves_roles_and_capture_time(tmp_path: Path) -> None:
    capture_path = tmp_path / "neutral.aistudio.json"
    capture_path.write_text(
        json.dumps(
            {
                "chunkedPrompt": {
                    "chunks": [
                        {"role": "user", "text": "A neutral question."},
                        {"role": "model", "text": "Internal trace.", "isThought": True},
                        {"role": "model", "text": "A neutral answer."},
                    ],
                    "pendingInputs": [{"role": "user", "text": "   "}],
                },
                "runSettings": {"model": "synthetic-gemini"},
                "systemInstruction": {},
            }
        ),
        encoding="utf-8",
    )

    capture = parse_browser_capture(capture_path)

    assert capture.parse_status == "parsed"
    assert capture.provider == "gemini"
    assert capture.conversation_id is None
    assert capture.capture_id == "neutral.aistudio"
    conversation = capture.conversations[0]
    assert conversation.created_at.value is None
    assert conversation.updated_at.value == capture.capture_mtime
    assert [message.role for message in conversation.messages] == [
        "user",
        "model_thought",
        "assistant",
    ]
    assert conversation.messages[1].model == "synthetic-gemini"
    assert conversation.messages[1].parent_message_id == conversation.messages[0].message_id
