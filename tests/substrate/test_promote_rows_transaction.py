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


def test_interrupted_repromote_does_not_delete_old_data() -> None:
    """sinnix-kx4 regression: a mid-generator failure must not wipe old rows.

    Before the staged-swap fix, promote_rows DELETEd the target refresh_id's
    rows in autocommit BEFORE consuming the row generator. A generator that
    raised partway through (e.g. the process got OOM-killed, or the live
    source hit a transient read error) left the target refresh_id with ZERO
    rows and no exception ever surfaced past this point in some call chains —
    this was the observed real-world failure on machine_cgroup_memory_sample.
    The fix stages new rows under a private refresh_id first and only swaps
    them onto the target after a full successful commit, so an interrupted
    re-promote leaves the previous good data completely untouched.
    """
    conn = _conn()
    promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                 rows=[1, 2, 3], extractor=lambda x: (x,))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3

    def _flaky_rows():
        yield 10
        yield 11
        raise RuntimeError("simulated interruption (e.g. OOM kill mid-generator)")

    with pytest.raises(RuntimeError):
        promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                     rows=_flaky_rows(), extractor=lambda x: (x,))

    # The old r1 rows must survive untouched — no DELETE for r1 ever committed
    # because the staged insert under a private id never finished.
    rows = conn.execute(
        "SELECT v FROM t WHERE refresh_id = 'r1' ORDER BY v"
    ).fetchall()
    assert rows == [(1,), (2,), (3,)]

    # No orphaned staging rows should be visible under any OTHER refresh_id
    # either (the failed transaction rolled the staged insert back too).
    total = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert total == 3

    # A subsequent successful re-promote of r1 still works normally.
    n = promote_rows(conn, table="t", columns=("v",), refresh_id="r1",
                     rows=[20, 21], extractor=lambda x: (x,))
    assert n == 2
    rows = conn.execute(
        "SELECT v FROM t WHERE refresh_id = 'r1' ORDER BY v"
    ).fetchall()
    assert rows == [(20,), (21,)]
