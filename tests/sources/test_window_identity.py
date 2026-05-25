"""Tests for the Hyprland window-identity capture reader.

Sinnix's aw-window-identity sidecar writes JSONL with per-window
``address``, ``pid``, ``class``, ``title``. This module exposes them
to lynchpin. The tests pin: filename routing, date range filtering,
field normalization, and graceful handling of malformed lines.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from lynchpin.sources.window_identity import (
    WindowIdentityEvent,
    iter_window_identity,
)


def _write_capture(root: Path, host: str, day: date, records: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{host}-{day.isoformat()}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_iter_yields_well_formed_events(tmp_path: Path) -> None:
    root = tmp_path / "window-identity"
    _write_capture(root, "sinnix-prime", date(2026, 5, 25), [
        {
            "ts": "2026-05-25T10:00:00.000+00:00",
            "host": "sinnix-prime",
            "event": "activewindowv2",
            "address": "0xdeadbeef",
            "pid": 12345,
            "class": "kitty",
            "title": "sinity-lynchpin",
            "workspace": "1",
            "monitor": "DP-1",
            "floating": False,
            "fullscreen": False,
        },
    ])

    events = list(iter_window_identity(root=root))
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, WindowIdentityEvent)
    assert e.host == "sinnix-prime"
    assert e.address == "0xdeadbeef"
    assert e.pid == 12345
    assert e.class_ == "kitty"
    assert e.title == "sinity-lynchpin"
    assert e.floating is False


def test_filename_date_filter_skips_files_outside_range(tmp_path: Path) -> None:
    """Files outside the queried date range must not be opened — cheap
    cheap skip based on filename alone."""
    root = tmp_path / "window-identity"
    _write_capture(root, "h1", date(2026, 5, 20), [
        {"ts": "2026-05-20T10:00:00+00:00", "host": "h1", "class": "X"},
    ])
    _write_capture(root, "h1", date(2026, 5, 25), [
        {"ts": "2026-05-25T10:00:00+00:00", "host": "h1", "class": "Y"},
    ])
    events = list(iter_window_identity(start=date(2026, 5, 24), end=date(2026, 5, 26), root=root))
    titles = [e.class_ for e in events]
    assert "Y" in titles
    assert "X" not in titles


def test_host_filter(tmp_path: Path) -> None:
    root = tmp_path / "window-identity"
    _write_capture(root, "host-a", date(2026, 5, 25), [
        {"ts": "2026-05-25T10:00:00+00:00", "host": "host-a", "class": "A"},
    ])
    _write_capture(root, "host-b", date(2026, 5, 25), [
        {"ts": "2026-05-25T10:00:00+00:00", "host": "host-b", "class": "B"},
    ])
    events = list(iter_window_identity(host="host-b", root=root))
    assert len(events) == 1
    assert events[0].host == "host-b"


def test_malformed_line_skipped(tmp_path: Path) -> None:
    """A corrupted JSON line must not abort iteration."""
    root = tmp_path / "window-identity"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "host-2026-05-25.jsonl"
    path.write_text(
        '{"ts": "2026-05-25T10:00:00+00:00", "host": "host", "class": "good"}\n'
        "not json at all\n"
        '{"ts": "2026-05-25T11:00:00+00:00", "host": "host", "class": "also-good"}\n'
    )
    events = list(iter_window_identity(root=root))
    classes = [e.class_ for e in events]
    assert classes == ["good", "also-good"]


def test_missing_root_yields_empty(tmp_path: Path) -> None:
    """If the capture root doesn't exist (sidecar not deployed), iteration
    is empty, not raising."""
    assert list(iter_window_identity(root=tmp_path / "does-not-exist")) == []


def test_event_without_pid_or_address_still_yielded(tmp_path: Path) -> None:
    """Some Hyprland events may carry partial data; emit them with None
    fields rather than dropping — caller decides whether to use."""
    root = tmp_path / "window-identity"
    _write_capture(root, "h", date(2026, 5, 25), [
        {"ts": "2026-05-25T10:00:00+00:00", "host": "h", "class": "X"},
    ])
    events = list(iter_window_identity(root=root))
    assert len(events) == 1
    assert events[0].address is None
    assert events[0].pid is None
    assert events[0].class_ == "X"
