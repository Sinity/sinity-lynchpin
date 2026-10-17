"""Schema setup must publish a complete schema in its transaction owner."""
from __future__ import annotations

import json
import time

import pytest

from lynchpin.substrate.connection import apply_schema, connect


class RecordingConnection:
    def __init__(self, inner, *, fail=False, fail_commit=False):
        self.inner = inner
        self.statements = []
        self.creates = 0
        self.fail = fail
        self.fail_commit = fail_commit

    def execute(self, sql, *args):
        self.statements.append(sql.strip())
        if self.fail_commit and sql.strip() == "COMMIT":
            raise RuntimeError("synthetic schema commit failure")
        if sql.strip().startswith("CREATE TABLE ") and "substrate_meta" not in sql:
            self.creates += 1
            if self.fail and self.creates == 2:
                raise RuntimeError("synthetic schema failure")
        return self.inner.execute(sql, *args)


def test_schema_setup_has_one_owned_commit(tmp_path):
    with connect(tmp_path / "schema.duckdb") as conn:
        recording = RecordingConnection(conn)
        started = time.monotonic()
        apply_schema(recording)
        elapsed = time.monotonic() - started
        print(json.dumps({"fresh_schema_seconds": elapsed, "ddl_creates": recording.creates}))
        assert recording.statements.count("BEGIN TRANSACTION") == 1
        assert recording.statements.count("COMMIT") == 1
        assert conn.execute("SELECT value FROM substrate_meta WHERE key='version'").fetchone()


@pytest.mark.parametrize("previous", [False, True])
def test_schema_failure_preserves_committed_preimage(tmp_path, previous):
    with connect(tmp_path / "schema.duckdb") as conn:
        if previous:
            conn.execute("CREATE TABLE substrate_meta(key VARCHAR PRIMARY KEY, value VARCHAR)")
            conn.execute("INSERT INTO substrate_meta VALUES ('version','0')")
            conn.execute("CREATE TABLE commit_fact(marker VARCHAR)")
            conn.execute("INSERT INTO commit_fact VALUES ('retained')")
        before = conn.execute("SELECT table_name, sql FROM duckdb_tables() WHERE schema_name='main' ORDER BY table_name").fetchall()
        with pytest.raises(RuntimeError, match="synthetic schema failure"):
            apply_schema(RecordingConnection(conn, fail=True))
        after = conn.execute("SELECT table_name, sql FROM duckdb_tables() WHERE schema_name='main' ORDER BY table_name").fetchall()
        assert after == before
        if previous:
            assert conn.execute("SELECT marker FROM commit_fact").fetchall() == [('retained',)]
            assert conn.execute("SELECT value FROM substrate_meta WHERE key='version'").fetchone() == ('0',)


def test_schema_setup_does_not_commit_callers_transaction(tmp_path):
    with connect(tmp_path / "schema.duckdb") as conn:
        conn.execute("BEGIN TRANSACTION")
        recording = RecordingConnection(conn)
        apply_schema(recording)
        assert "COMMIT" not in recording.statements
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT table_name FROM duckdb_tables() WHERE schema_name='main'").fetchall() == []


def test_schema_failure_leaves_callers_transaction_under_caller_control(tmp_path):
    with connect(tmp_path / "schema.duckdb") as conn:
        conn.execute("BEGIN TRANSACTION")
        conn.execute("CREATE TABLE caller_canary(value INTEGER)")
        conn.execute("INSERT INTO caller_canary VALUES (7)")
        recording = RecordingConnection(conn, fail=True)
        with pytest.raises(RuntimeError, match="synthetic schema failure"):
            apply_schema(recording)
        assert "COMMIT" not in recording.statements
        assert "ROLLBACK" not in recording.statements
        assert conn.execute("SELECT value FROM caller_canary").fetchall() == [(7,)]
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT table_name FROM duckdb_tables() WHERE schema_name='main'").fetchall() == []


@pytest.mark.parametrize("previous", [False, True])
def test_schema_commit_failure_rolls_back_owned_setup(tmp_path, previous):
    with connect(tmp_path / "schema.duckdb") as conn:
        if previous:
            conn.execute("CREATE TABLE substrate_meta(key VARCHAR PRIMARY KEY, value VARCHAR)")
            conn.execute("INSERT INTO substrate_meta VALUES ('version','0')")
            conn.execute("CREATE TABLE commit_fact(marker VARCHAR)")
            conn.execute("INSERT INTO commit_fact VALUES ('retained')")
        before = conn.execute("SELECT table_name, sql FROM duckdb_tables() WHERE schema_name='main' ORDER BY table_name").fetchall()
        with pytest.raises(RuntimeError, match="synthetic schema commit failure"):
            apply_schema(RecordingConnection(conn, fail_commit=True))
        assert conn.execute("SELECT table_name, sql FROM duckdb_tables() WHERE schema_name='main' ORDER BY table_name").fetchall() == before
        if previous:
            assert conn.execute("SELECT marker FROM commit_fact").fetchall() == [('retained',)]
