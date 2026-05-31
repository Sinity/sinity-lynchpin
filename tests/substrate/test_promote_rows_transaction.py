"""Regression: promote_rows must wrap its DELETE+INSERT batch in ONE transaction.

In DuckDB autocommit, executemany commits (and fsyncs) per row — measured at
~120KB-1.8MB written/row and ~99% of substrate-promote wall-time (artifacts
promote: 20GB / 14min for ~163k rows; one transaction -> 179MB / 55s). This pins
the transaction contract (standalone owns + commits, caller-owned defers,
failure rolls back without poisoning the connection) rather than fragile
byte/timing numbers.
"""

from __future__ import annotations

import duckdb
import pytest

from lynchpin.substrate._helpers import promote_rows


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    # PRIMARY KEY on (v, refresh_id): exercises DuckDB's index limitation where a
    # DELETE-then-INSERT of the same key inside one transaction trips a phantom
    # duplicate-key error — so the DELETE must commit before the INSERT txn.
    conn.execute("CREATE TABLE t (refresh_id VARCHAR, v INTEGER, PRIMARY KEY (v, refresh_id))")
    return conn


def test_re_promote_same_refresh_id_replaces_without_pk_violation() -> None:
    conn = _conn()
    promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                 rows=[1, 2, 3], extractor=lambda x: (x,))
    # idempotent re-promote of the same refresh_id: DELETE (committed) then
    # re-INSERT the same keys must NOT raise a primary-key ConstraintException.
    n = promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                     rows=[1, 2, 3], extractor=lambda x: (x,))
    assert n == 3
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_standalone_owns_and_commits_transaction() -> None:
    conn = _conn()
    n = promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                     rows=[1, 2, 3], extractor=lambda x: (x,))
    assert n == 3
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_nested_defers_commit_to_caller() -> None:
    conn = _conn()
    conn.execute("BEGIN TRANSACTION")
    # caller owns the transaction; promote_rows must NOT BEGIN/COMMIT (a nested
    # BEGIN would abort the caller's transaction).
    n = promote_rows(conn, table="t", columns=("v",), refresh_id="r2",
                     rows=[4, 5], extractor=lambda x: (x,),
                     delete_existing=False, wrap_transaction=False)
    assert n == 2
    # not yet visible-committed until the caller commits
    conn.execute("COMMIT")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_failure_rolls_back_and_leaves_connection_usable() -> None:
    conn = _conn()
    promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                 rows=[1, 2], extractor=lambda x: (x,))
    # arity mismatch (extractor yields 1 value, 2 columns declared) -> the
    # promote fails inside the owned transaction. The exact type varies by
    # insert path (duckdb.Error / pandas ValueError); the contract is that it
    # raises AND rolls back, leaving the connection usable.
    with pytest.raises(Exception):
        promote_rows(conn, table="t", columns=("v", "missing"), refresh_id="r3",
                     rows=[1], extractor=lambda x: (x,))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    # connection not stuck in an aborted transaction: a subsequent promote works
    promote_rows(conn, table="t", columns=("v",), refresh_id="r4",
                 rows=[9], extractor=lambda x: (x,))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
