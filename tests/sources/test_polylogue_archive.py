"""Neutral fixtures for the direct Polylogue archive adapter."""

from __future__ import annotations

from contextlib import contextmanager
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
            CREATE TABLE raw_sessions (raw_id TEXT PRIMARY KEY, origin TEXT, native_id TEXT, blob_hash BLOB);
            CREATE TABLE blob_refs (blob_hash BLOB, ref_id TEXT, ref_type TEXT);
            """
        )
        conn.execute("INSERT INTO provider_identities VALUES (?, ?, ?)", ("provider-codex", "codex", "synthetic-provider"))
        conn.execute("INSERT INTO raw_sessions VALUES (?, ?, ?, ?)", ("raw-session", "codex-session", "synthetic-session", b"raw-hash"))
        conn.execute("INSERT INTO blob_refs VALUES (?, ?, ?)", (b"raw-hash", "raw-session", "raw_session"))
    with sqlite3.connect(root / "index.db") as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (native_id TEXT, origin TEXT, parent_session_id TEXT, created_at_ms INTEGER, updated_at_ms INTEGER);
            CREATE TABLE messages (session_id TEXT, native_id TEXT, role TEXT, occurred_at_ms INTEGER, content_hash BLOB);
            CREATE TABLE blocks (block_id TEXT, message_id TEXT, block_type TEXT, content_hash BLOB);
            CREATE TABLE session_links (src_session_id TEXT, dst_origin TEXT, dst_native_id TEXT, link_type TEXT, resolved_dst_session_id TEXT);
            CREATE VIRTUAL TABLE messages_fts USING fts5(message_id, text);
            """
        )
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)", ("synthetic-session", "codex-session", "claude-code-session:parent", 1767225600000, 1767225660000))
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", ("codex-session:synthetic-session", "synthetic-message", "user", 1767225600000, b"message-hash"))
        conn.execute("INSERT INTO blocks VALUES (?, ?, ?, ?)", ("synthetic-block", "codex-session:synthetic-session:synthetic-message", "text", b"block-hash"))
        conn.execute("INSERT INTO session_links VALUES (?, ?, ?, ?, ?)", ("codex-session:synthetic-session", "claude-code-session", "parent", "fork", None))
    with sqlite3.connect(root / "embeddings.db") as conn:
        conn.executescript(
            """
            CREATE TABLE embedding_status (session_id TEXT, origin TEXT, message_count_embedded INTEGER);
            CREATE TABLE embedding_derivation_state (session_id TEXT, origin TEXT, attempt_state TEXT);
            """
        )
    return root


def test_direct_archive_routes_normalized_capabilities_to_index(tmp_path) -> None:
    root = _archive_root(tmp_path)
    index_snapshot = archive.snapshot_capability("sessions", tmp_path / "private-run", root)

    assert archive.database_for_capability("raw_sessions", root).name == "source"
    assert archive.database_for_capability("sessions", root).name == "index"
    assert archive.database_for_capability("messages", root).name == "index"
    assert archive.database_for_capability("blocks", root).name == "index"
    assert archive.database_for_capability("lineage", root).name == "index"
    assert archive.database_for_capability("embedding_status", root).name == "embeddings"

    assert list(archive.iter_sessions(index_snapshot)) == [archive.ArchiveSession("codex-session:synthetic-session", None, "codex-session", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "claude-code-session:parent")]
    assert list(archive.iter_messages(index_snapshot)) == [archive.ArchiveMessage("codex-session:synthetic-session:synthetic-message", "codex-session:synthetic-session", "user", "user", "2026-01-01T00:00:00Z", "6d6573736167652d68617368")]
    assert list(archive.iter_blocks(index_snapshot)) == [archive.ArchiveBlock("synthetic-block", "codex-session:synthetic-session:synthetic-message", "text", "626c6f636b2d68617368")]
    assert list(archive.iter_lineages(index_snapshot)) == [archive.ArchiveLineage("codex-session:synthetic-session", "claude-code-session:parent", "fork")]


def test_readiness_introspects_without_scanning_timestamp_coverage(tmp_path) -> None:
    report = archive.readiness(_archive_root(tmp_path))

    assert report.status == "ready"
    assert report.coverage.status == "unknown"
    source = next(schema for schema in report.schemas if schema.database.name == "source")
    index = next(schema for schema in report.schemas if schema.database.name == "index")
    embeddings = next(schema for schema in report.schemas if schema.database.name == "embeddings")
    assert source.capabilities.raw_sessions and source.capabilities.blob_references
    assert index.capabilities.sessions and index.capabilities.messages and index.capabilities.blocks
    assert index.capabilities.lineage and index.capabilities.fts
    assert embeddings.capabilities.embedding_status and embeddings.capabilities.embedding_metadata
    assert dict(index.table_columns)["sessions"] == (
        "native_id", "origin", "parent_session_id", "created_at_ms", "updated_at_ms"
    )


def test_readiness_retains_capabilities_when_virtual_table_xinfo_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    root = _archive_root(tmp_path)
    embeddings = root / "embeddings.db"
    with sqlite3.connect(embeddings) as conn:
        conn.execute("CREATE TABLE unavailable_vector (embedding_id TEXT)")

    original_open_readonly = archive.open_readonly

    class FailingXinfoConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args, **kwargs):
            if statement == 'PRAGMA table_xinfo("unavailable_vector")':
                raise sqlite3.OperationalError("no such module: vec0")
            return self._connection.execute(statement, *args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    @contextmanager
    def failing_open_readonly(path):
        with original_open_readonly(path) as connection:
            yield FailingXinfoConnection(connection)

    monkeypatch.setattr(archive, "open_readonly", failing_open_readonly)

    report = archive.readiness(root)

    assert report.status == "ready"
    assert (
        report.reason
        == "source authority and normalized index capabilities are available"
    )
    assert {schema.database.name for schema in report.schemas} == {
        "source",
        "index",
        "embeddings",
    }
    assert all(
        schema.capabilities.raw_sessions
        for schema in report.schemas
        if schema.database.name == "source"
    )
    assert all(
        schema.capabilities.sessions and schema.capabilities.messages
        for schema in report.schemas
        if schema.database.name == "index"
    )
    embedding_schema = next(schema for schema in report.schemas if schema.database.name == "embeddings")
    assert "unavailable_vector" in embedding_schema.tables
    assert dict(embedding_schema.table_columns)["unavailable_vector"] is None
    assert embedding_schema.table_introspection_caveats == (
        archive.TableIntrospectionCaveat(
            table="unavailable_vector",
            operation="table_xinfo",
            reason="no such module: vec0",
        ),
    )
    assert embedding_schema.capabilities.embedding_status
    assert embedding_schema.capabilities.embedding_metadata


def test_snapshot_is_stable_and_readers_remain_read_only(tmp_path) -> None:
    root = _archive_root(tmp_path)
    snapshot = archive.snapshot_capability("messages", tmp_path / "private-run", root)
    with sqlite3.connect(root / "index.db") as conn:
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", ("codex-session:synthetic-session", "later", "assistant", 1767225720000, b"later"))

    assert "codex-session:synthetic-session:later" not in {row.locator for row in archive.iter_messages(snapshot)}
    with archive.open_readonly(snapshot.path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM messages")


def test_schema_mismatch_and_missing_archive_degrade_through_typed_results(tmp_path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    with sqlite3.connect(root / "source.db") as conn:
        conn.execute("CREATE TABLE raw_sessions (raw_id TEXT)")
    with sqlite3.connect(root / "index.db") as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")
    index = next(item for item in archive.archive_databases(root) if item.name == "index")
    snapshot = archive.snapshot_database(index, tmp_path / "private-run")

    assert archive.readiness(root).status == "degraded"
    with pytest.raises(archive.PolylogueArchiveSchemaError):
        list(archive.iter_messages(snapshot))
    assert archive.readiness(tmp_path / "absent").status == "missing"


def test_root_resolution_honors_polylogue_archive_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("POLYLOGUE_ARCHIVE_ROOT", str(tmp_path / "configured"))

    assert archive.resolve_archive_root() == tmp_path / "configured"
