"""Tests for ai_tool_usage MCP tool (Fix #46, Task #2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def polylogue_db_fixture(tmp_path: Path) -> Path:
    """Create a minimal polylogue database with action_events table."""
    db_path = tmp_path / "polylogue.db"
    conn = sqlite3.connect(db_path)

    # Create the action_events table with the essential columns
    conn.execute("""
        CREATE TABLE action_events (
            event_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            timestamp TEXT,
            action_kind TEXT NOT NULL,
            normalized_tool_name TEXT NOT NULL
        )
    """)

    # Insert test data
    test_data = [
        ("ev-1", "conv-1", "msg-1", "2026-05-20T10:00:00Z", "agent", "edit"),
        ("ev-2", "conv-1", "msg-2", "2026-05-20T10:30:00Z", "agent", "bash"),
        ("ev-3", "conv-2", "msg-3", "2026-05-20T11:00:00Z", "shell", "bash"),
        ("ev-4", "conv-2", "msg-4", "2026-05-20T11:30:00Z", "shell", "other"),
        ("ev-5", "conv-3", "msg-5", "2026-05-20T12:00:00Z", "file_read", "read"),
        ("ev-6", "conv-4", "msg-6", "2026-05-21T10:00:00Z", "agent", "write"),
    ]

    for event_id, conv_id, msg_id, ts, action_kind, tool_name in test_data:
        conn.execute(
            """
            INSERT INTO action_events
            (event_id, conversation_id, message_id, timestamp, action_kind, normalized_tool_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, conv_id, msg_id, ts, action_kind, tool_name),
        )

    conn.commit()
    conn.close()
    return db_path


def test_ai_tool_usage_returns_action_kinds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, polylogue_db_fixture: Path) -> None:
    """Test that ai_tool_usage returns action_kind distribution."""
    import lynchpin.core.config as cfg_mod
    from lynchpin.mcp.tools.change import ai_tool_usage

    # Reset config cache and monkeypatch get_config in the config module
    cfg_mod._CONFIG = None

    def mock_get_config():
        config = cfg_mod.LynchpinConfig.from_env()
        # Use a dataclass replace-like approach
        import dataclasses
        return dataclasses.replace(config, polylogue_db=polylogue_db_fixture)

    monkeypatch.setattr("lynchpin.core.config.get_config", mock_get_config)

    result = ai_tool_usage()

    assert result["degraded"] is False
    assert result["reason"] is None
    assert len(result["rows"]) > 0

    # Check that we have action_kind entries
    action_kinds = {row["action_kind"] for row in result["rows"]}
    assert "agent" in action_kinds
    assert "shell" in action_kinds
    assert "file_read" in action_kinds

    # Verify counts
    agent_row = next((r for r in result["rows"] if r["action_kind"] == "agent"), None)
    assert agent_row is not None
    assert agent_row["count"] == 3  # 3 agent events (ev-1, ev-2, ev-6)
    assert agent_row["sessions"] == 2  # 2 distinct conversations (conv-1, conv-4)


def test_ai_tool_usage_with_date_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, polylogue_db_fixture: Path
) -> None:
    """Test that ai_tool_usage respects start and end date parameters."""
    import lynchpin.core.config as cfg_mod
    from lynchpin.mcp.tools.change import ai_tool_usage

    # Reset config cache and monkeypatch get_config in the config module
    cfg_mod._CONFIG = None

    def mock_get_config():
        config = cfg_mod.LynchpinConfig.from_env()
        import dataclasses
        return dataclasses.replace(config, polylogue_db=polylogue_db_fixture)

    monkeypatch.setattr("lynchpin.core.config.get_config", mock_get_config)

    # Query only 2026-05-20 data
    result = ai_tool_usage(start="2026-05-20T00:00:00Z", end="2026-05-20T23:59:59Z")

    assert result["degraded"] is False
    # Should have 5 rows (all except the 2026-05-21 entry)
    total_count = sum(row["count"] for row in result["rows"])
    assert total_count == 5


def test_ai_tool_usage_degraded_on_missing_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that ai_tool_usage degrades gracefully when database is missing."""
    import lynchpin.core.config as cfg_mod
    from lynchpin.mcp.tools.change import ai_tool_usage

    # Reset config cache and monkeypatch get_config to point to a non-existent database
    cfg_mod._CONFIG = None

    def mock_get_config():
        config = cfg_mod.LynchpinConfig.from_env()
        import dataclasses
        return dataclasses.replace(config, polylogue_db=tmp_path / "nonexistent.db")

    monkeypatch.setattr("lynchpin.core.config.get_config", mock_get_config)

    result = ai_tool_usage()

    assert result["degraded"] is True
    assert "not found" in result["reason"]
    assert result["rows"] == []
