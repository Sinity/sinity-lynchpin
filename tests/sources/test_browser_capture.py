from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lynchpin.sources.browser_capture import inventory_browser_captures, parse_browser_capture


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
