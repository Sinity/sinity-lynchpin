"""Tests for clipboard and IRC evidence-source node builders."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from lynchpin.graph.evidence_clipboard import add_clipboard
from lynchpin.graph.evidence_irc import add_irc


# ---------------------------------------------------------------------------
# Clipboard tests
# ---------------------------------------------------------------------------

def _make_clip(text: str, recorded_at: datetime, kind: str = "text", pinned: bool = False) -> Any:
    return SimpleNamespace(
        recorded_at=recorded_at,
        value=text,
        source="/fake/clipboard.json",
        file_path=None,
        pinned=pinned,
        kind=kind,
        date=recorded_at.date(),
    )


_CLIP_TS = datetime(2026, 5, 15, 10, 30, 0, tzinfo=timezone.utc)


def test_add_clipboard_emits_nodes_for_range(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = _make_clip("some text copied", _CLIP_TS)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: [clip],
    )

    nodes: list = []
    add_clipboard(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert len(nodes) == 1
    node = nodes[0]
    assert node.kind == "clipboard_entry"
    assert node.source == "clipboard"
    assert node.date == _CLIP_TS.date()
    assert node.start == _CLIP_TS
    assert "some text copied" in node.summary


def test_add_clipboard_node_payload_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = _make_clip("https://example.com", _CLIP_TS, kind="url", pinned=True)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: [clip],
    )

    nodes: list = []
    add_clipboard(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert len(nodes) == 1
    payload = nodes[0].payload
    assert payload is not None
    assert payload["kind"] == "url"
    assert payload["pinned"] is True
    assert "example.com" in payload["value"]


def test_add_clipboard_skips_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = _make_clip("", _CLIP_TS)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: [clip],
    )

    nodes: list = []
    add_clipboard(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert nodes == []


def test_add_clipboard_respects_selected_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """When selected is non-empty and no project is detected, no node is emitted."""
    clip = _make_clip("unrelated copied text", _CLIP_TS)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: [clip],
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.projects_mentioned_in_text",
        lambda text: (),
    )

    nodes: list = []
    add_clipboard(
        nodes,
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        selected={"sinity-lynchpin"},
    )

    assert nodes == []


def test_add_clipboard_no_source_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: [],
    )

    nodes: list = []
    add_clipboard(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert nodes == []


# ---------------------------------------------------------------------------
# IRC tests
# ---------------------------------------------------------------------------

def _make_conv(
    conversation_id: str,
    channel: str,
    start: datetime,
    end: datetime,
    total_lines: int = 10,
    sinity_lines: int = 3,
    mention_lines: int = 2,
    messages: tuple = (),
) -> Any:
    return SimpleNamespace(
        conversation_id=conversation_id,
        channel=channel,
        start=start,
        end=end,
        source_path="/fake/irc.log",
        total_lines=total_lines,
        sinity_lines=sinity_lines,
        mention_lines=mention_lines,
        messages=messages,
        date=start.date(),
    )


_IRC_START = datetime(2026, 5, 15, 14, 0, 0, tzinfo=timezone.utc)
_IRC_END = datetime(2026, 5, 15, 15, 30, 0, tzinfo=timezone.utc)


def test_add_irc_emits_nodes_for_range(monkeypatch: pytest.MonkeyPatch) -> None:
    conv = _make_conv("42", "#dev", _IRC_START, _IRC_END)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range",
        lambda **kwargs: [conv],
    )

    nodes: list = []
    add_irc(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert len(nodes) == 1
    node = nodes[0]
    assert node.kind == "irc_conversation"
    assert node.source == "irc"
    assert node.date == _IRC_START.date()
    assert node.start == _IRC_START
    assert node.end == _IRC_END
    assert "#dev" in node.summary


def test_add_irc_node_payload_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    conv = _make_conv("99", "#sinity-lynchpin", _IRC_START, _IRC_END, total_lines=50, sinity_lines=20)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range",
        lambda **kwargs: [conv],
    )

    nodes: list = []
    add_irc(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert len(nodes) >= 1
    payload = nodes[0].payload
    assert payload is not None
    assert payload["conversation_id"] == "99"
    assert payload["total_lines"] == 50
    assert payload["sinity_lines"] == 20
    assert payload["channel"] == "#sinity-lynchpin"


def test_add_irc_respects_selected_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """When selected is non-empty and no project is detected, no node is emitted."""
    conv = _make_conv("1", "#random", _IRC_START, _IRC_END)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range",
        lambda **kwargs: [conv],
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.projects_mentioned_in_text",
        lambda text: (),
    )

    nodes: list = []
    add_irc(
        nodes,
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        selected={"sinity-lynchpin"},
    )

    assert nodes == []


def test_add_irc_no_source_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range",
        lambda **kwargs: [],
    )

    nodes: list = []
    add_irc(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    assert nodes == []


def test_add_irc_node_id_is_unique_per_conv(monkeypatch: pytest.MonkeyPatch) -> None:
    convs = [
        _make_conv("1", "#dev", _IRC_START, _IRC_END),
        _make_conv("2", "#dev", _IRC_START, _IRC_END),
    ]
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range",
        lambda **kwargs: convs,
    )

    nodes: list = []
    add_irc(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())

    ids = [n.id for n in nodes]
    assert len(ids) == len(set(ids)), "Node IDs must be unique"
