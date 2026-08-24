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
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, native_id TEXT, origin TEXT, raw_id TEXT,
                parent_session_id TEXT, created_at_ms INTEGER, updated_at_ms INTEGER,
                authored_user_message_count INTEGER
            );
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY, session_id TEXT, native_id TEXT, role TEXT,
                material_origin TEXT, occurred_at_ms INTEGER, content_hash BLOB
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY, message_id TEXT, block_type TEXT, text TEXT, content_hash BLOB
            );
            CREATE TABLE session_links (src_session_id TEXT, dst_origin TEXT, dst_native_id TEXT, link_type TEXT, resolved_dst_session_id TEXT);
            CREATE VIRTUAL TABLE messages_fts USING fts5(message_id, text);
            CREATE TABLE fts_freshness_state (
                surface TEXT, state TEXT, checked_at TEXT, source_rows INTEGER, indexed_rows INTEGER,
                missing_rows INTEGER, excess_rows INTEGER, duplicate_rows INTEGER, detail TEXT,
                identity_mismatch_rows INTEGER
            );
            """
        )
        origins = ("chatgpt", "claude-ai", "gemini", "claude-code", "codex")
        for index, origin in enumerate(origins):
            session_id = f"{origin}:synthetic-session"
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    "synthetic-session",
                    origin,
                    f"raw-{origin}",
                    "claude-code:parent" if origin == "codex" else None,
                    1767225600000 + index * 60_000,
                    1767225660000 + index * 60_000,
                    1,
                ),
            )
            message_id = f"{session_id}:direct"
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, session_id, "direct", "user", "operator_direct", 1767225600000 + index * 60_000, b"message-hash"),
            )
            conn.execute(
                "INSERT INTO blocks VALUES (?, ?, ?, ?, ?)",
                (f"block-{origin}", message_id, "text", f"direct text {origin}", b"block-hash"),
            )
        codex_session = "codex:synthetic-session"
        for native_id, role, material_origin in (
            ("pasted", "user", "pasted"),
            ("model", "assistant", "model_generated"),
            ("tool", "tool", "tool_generated"),
            ("uncertain", "user", None),
        ):
            message_id = f"{codex_session}:{native_id}"
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, codex_session, native_id, role, material_origin, 1767225900000, b"other-message-hash"),
            )
            conn.execute(
                "INSERT INTO blocks VALUES (?, ?, ?, ?, ?)",
                (f"block-{native_id}", message_id, "text", f"{native_id} text", b"other-block-hash"),
            )
        conn.execute("INSERT INTO session_links VALUES (?, ?, ?, ?, ?)", (codex_session, "claude-code", "parent", "fork", None))
        conn.execute(
            "INSERT INTO fts_freshness_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("messages", "stale", "2026-01-01T00:10:00Z", 9, 5, 4, 0, 0, "synthetic stale index", 0),
        )
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

    sessions = list(archive.iter_sessions(index_snapshot))
    messages = list(archive.iter_messages(index_snapshot))
    blocks = list(archive.iter_blocks(index_snapshot))

    assert {session.origin for session in sessions} == {
        "chatgpt",
        "claude-ai",
        "gemini",
        "claude-code",
        "codex",
    }
    assert len(messages) == 9
    assert len(blocks) == 9
    assert list(archive.iter_lineages(index_snapshot)) == [archive.ArchiveLineage("codex:synthetic-session", "claude-code:parent", "fork")]


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
    assert {"origin", "created_at_ms", "updated_at_ms", "authored_user_message_count"} <= set(dict(index.table_columns)["sessions"] or ())
    assert {"role", "material_origin", "occurred_at_ms"} <= set(dict(index.table_columns)["messages"] or ())


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
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("codex:synthetic-session:later", "codex:synthetic-session", "later", "assistant", "model_generated", 1767225720000, b"later"),
        )

    assert "codex:synthetic-session:later" not in {row.locator for row in archive.iter_messages(snapshot)}
    with archive.open_readonly(snapshot.path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM messages")


def test_coverage_summary_stratifies_origins_and_bounds_freshness(tmp_path) -> None:
    root = _archive_root(tmp_path)
    snapshot = archive.snapshot_capability("messages", tmp_path / "private-run", root)

    coverage = archive.coverage_summary(snapshot)

    assert coverage.status == "bounded"
    assert coverage.collection_model == "incremental_archive"
    assert coverage.session_count == 5
    assert coverage.message_count == 9
    assert coverage.authored_user_message_count == 5
    assert coverage.min_timestamp == "2026-01-01T00:00:00Z"
    assert coverage.max_timestamp == "2026-01-01T00:05:00Z"
    assert {(item.origin, item.session_count, item.message_count, item.authored_user_message_count) for item in coverage.origins} == {
        ("chatgpt", 1, 1, 1),
        ("claude-ai", 1, 1, 1),
        ("gemini", 1, 1, 1),
        ("claude-code", 1, 1, 1),
        ("codex", 1, 5, 1),
    }


def test_coverage_summary_marks_unsupported_schema_unknown(tmp_path) -> None:
    root = _archive_root(tmp_path)
    with sqlite3.connect(root / "index.db") as conn:
        conn.execute("ALTER TABLE messages RENAME COLUMN occurred_at_ms TO unsupported_time")
    snapshot = archive.snapshot_capability("messages", tmp_path / "private-run", root)

    coverage = archive.coverage_summary(snapshot)

    assert coverage.status == "unknown"
    assert "occurred_at_ms" in coverage.reason


def test_fts_readiness_reports_stale_and_ready_counts_without_rebuild(tmp_path) -> None:
    root = _archive_root(tmp_path)
    stale_snapshot = archive.snapshot_capability("fts", tmp_path / "stale-run", root)

    stale = archive.fts_readiness(stale_snapshot)

    assert stale.status == "stale"
    assert stale.surfaces == (
        archive.ArchiveFtsSurfaceReadiness(
            surface="messages",
            state="stale",
            checked_at="2026-01-01T00:10:00Z",
            source_row_count=9,
            fts_row_count=5,
            missing_row_count=4,
            excess_row_count=0,
            duplicate_row_count=0,
            identity_mismatch_row_count=0,
            detail="synthetic stale index",
        ),
    )
    with sqlite3.connect(root / "index.db") as conn:
        conn.execute(
            "UPDATE fts_freshness_state SET state = 'ready', indexed_rows = source_rows, missing_rows = 0"
        )
    ready_snapshot = archive.snapshot_capability("fts", tmp_path / "ready-run", root)

    ready = archive.fts_readiness(ready_snapshot)

    assert ready.status == "ready"
    assert ready.surfaces[0].source_row_count == ready.surfaces[0].fts_row_count == 9


def test_user_authored_content_units_preserve_authorship_and_raw_locators(tmp_path) -> None:
    root = _archive_root(tmp_path)
    snapshot = archive.snapshot_capability("messages", tmp_path / "private-run", root)

    direct = list(archive.iter_user_authored_content_units(snapshot))
    uncertain = list(archive.iter_uncertain_authorship_content_units(snapshot))

    assert {unit.session_origin for unit in direct} == {
        "chatgpt",
        "claude-ai",
        "gemini",
        "claude-code",
        "codex",
    }
    assert {unit.material_origin for unit in direct} == {"operator_direct"}
    assert {unit.text for unit in direct}.isdisjoint({"pasted text", "model text", "tool text"})
    codex = next(unit for unit in direct if unit.session_origin == "codex")
    assert codex.session_native_id == "synthetic-session"
    assert codex.raw_session_locator == "raw-codex"
    assert codex.block_locator == "block-codex"
    assert codex.block_kind == "text"
    assert codex.content_hash == "626c6f636b2d68617368"
    assert codex.occurred_at == "2026-01-01T00:04:00Z"
    assert uncertain == [
        archive.ArchiveContentUnit(
            locator="codex:synthetic-session:uncertain:block-uncertain",
            message_locator="codex:synthetic-session:uncertain",
            block_locator="block-uncertain",
            session_locator="codex:synthetic-session",
            session_origin="codex",
            session_native_id="synthetic-session",
            raw_session_locator="raw-codex",
            role="user",
            material_origin=None,
            authorship="uncertain",
            authorship_reason="user role is present but material_origin does not prove direct operator authorship",
            occurred_at="2026-01-01T00:05:00Z",
            text="uncertain text",
            block_kind="text",
            content_hash="6f746865722d626c6f636b2d68617368",
        )
    ]


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
