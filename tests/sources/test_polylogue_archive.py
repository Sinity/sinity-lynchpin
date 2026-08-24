"""Neutral fixtures for the direct Polylogue archive adapter."""

from __future__ import annotations

import sqlite3

import pytest

from lynchpin.sources import polylogue_archive as archive


def _archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    with sqlite3.connect(root / "source.db") as conn:
        conn.executescript(
            """
            CREATE TABLE provider_identities (provider_identity_id TEXT PRIMARY KEY, provider TEXT, external_id TEXT);
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, provider_identity_id TEXT, origin TEXT, started_at TEXT, updated_at TEXT, parent_session_id TEXT);
            CREATE TABLE messages (message_id TEXT PRIMARY KEY, session_id TEXT, role TEXT, author_kind TEXT, occurred_at TEXT, content_hash TEXT);
            CREATE TABLE blocks (block_id TEXT PRIMARY KEY, message_id TEXT, kind TEXT, content_hash TEXT);
            CREATE TABLE conversation_lineage (child_session_id TEXT, parent_session_id TEXT, relation TEXT);
            CREATE VIRTUAL TABLE message_fts USING fts5(message_id, body);
            """
        )
        for provider in ("chatgpt", "claude-ai", "gemini", "claude-code", "codex"):
            conn.execute("INSERT INTO provider_identities VALUES (?, ?, ?)", (f"provider-{provider}", provider, f"synthetic-{provider}"))
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)", (f"session-{provider}", f"provider-{provider}", "fixture", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", None))
            conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (f"message-{provider}", f"session-{provider}", "user", "operator", "2026-01-01T00:00:00Z", f"hash-{provider}"))
            conn.execute("INSERT INTO blocks VALUES (?, ?, ?, ?)", (f"block-{provider}", f"message-{provider}", "text", f"block-hash-{provider}"))
        conn.execute("INSERT INTO conversation_lineage VALUES (?, ?, ?)", ("session-codex", "session-claude-code", "fork"))
    with sqlite3.connect(root / "index.db") as conn:
        conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(content)")
    with sqlite3.connect(root / "embeddings.db") as conn:
        conn.execute("CREATE TABLE embeddings (message_id TEXT, vector BLOB)")
    return root


def test_direct_archive_snapshot_reads_supported_provider_shapes(tmp_path) -> None:
    root = _archive_root(tmp_path)
    source = next(item for item in archive.archive_databases(root) if item.name == "source")

    snapshot = archive.snapshot_database(source, tmp_path / "private-run")

    assert {row.provider for row in archive.iter_provider_identities(snapshot)} == {"chatgpt", "claude-ai", "gemini", "claude-code", "codex"}
    assert {row.locator for row in archive.iter_sessions(snapshot)} == {"session-chatgpt", "session-claude-ai", "session-gemini", "session-claude-code", "session-codex"}
    assert {row.author_kind for row in archive.iter_messages(snapshot)} == {"operator"}
    assert {row.kind for row in archive.iter_blocks(snapshot)} == {"text"}
    assert list(archive.iter_lineages(snapshot)) == [archive.ArchiveLineage("session-codex", "session-claude-code", "fork")]


def test_readiness_introspects_without_scanning_timestamp_coverage(tmp_path) -> None:
    report = archive.readiness(_archive_root(tmp_path))

    assert report.status == "ready"
    assert report.coverage.status == "unknown"
    source = next(schema for schema in report.schemas if schema.database.name == "source")
    assert source.capabilities == archive.ArchiveCapabilities(True, True, True, True, True, True)
    assert dict(source.table_columns)["messages"] == (
        "message_id", "session_id", "role", "author_kind", "occurred_at", "content_hash"
    )


def test_snapshot_is_stable_and_readers_remain_read_only(tmp_path) -> None:
    root = _archive_root(tmp_path)
    source = next(item for item in archive.archive_databases(root) if item.name == "source")
    snapshot = archive.snapshot_database(source, tmp_path / "private-run")
    with sqlite3.connect(source.path) as conn:
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", ("message-later", "session-codex", "assistant", "model", "2026-01-01T00:02:00Z", "later"))

    assert "message-later" not in {row.locator for row in archive.iter_messages(snapshot)}
    with archive.open_readonly(snapshot.path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM messages")


def test_schema_mismatch_and_missing_archive_degrade_through_typed_results(tmp_path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    with sqlite3.connect(root / "source.db") as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")
    source = next(item for item in archive.archive_databases(root) if item.name == "source")
    snapshot = archive.snapshot_database(source, tmp_path / "private-run")

    assert archive.readiness(root).status == "degraded"
    with pytest.raises(archive.PolylogueArchiveSchemaError):
        list(archive.iter_messages(snapshot))
    assert archive.readiness(tmp_path / "absent").status == "missing"


def test_root_resolution_honors_polylogue_archive_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("POLYLOGUE_ARCHIVE_ROOT", str(tmp_path / "configured"))

    assert archive.resolve_archive_root() == tmp_path / "configured"
