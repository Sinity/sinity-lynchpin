from __future__ import annotations

from dataclasses import dataclass

from lynchpin.substrate._helpers import promote_rows


@dataclass(frozen=True)
class _Row:
    label: str
    n: int


def _setup(conn):
    conn.execute("""
        CREATE TABLE t_demo (
            label VARCHAR NOT NULL,
            n INTEGER NOT NULL,
            refresh_id VARCHAR NOT NULL,
            PRIMARY KEY (label, refresh_id)
        )
    """)


def test_promote_rows_inserts_typed_rows():
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        n = promote_rows(
            conn,
            table="t_demo",
            columns=("label", "n"),
            refresh_id="r1",
            rows=[_Row("a", 1), _Row("b", 2)],
            extractor=lambda r: (r.label, r.n),
        )
        assert n == 2
        rows = conn.execute("SELECT label, n, refresh_id FROM t_demo ORDER BY label").fetchall()
        assert rows == [("a", 1, "r1"), ("b", 2, "r1")]


def test_promote_rows_idempotent_on_refresh_id():
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        promote_rows(conn, table="t_demo", columns=("label", "n"),
                     refresh_id="r1", rows=[_Row("a", 1), _Row("b", 2)],
                     extractor=lambda r: (r.label, r.n))
        # Re-running same refresh with one row deletes the previous two.
        n = promote_rows(conn, table="t_demo", columns=("label", "n"),
                         refresh_id="r1", rows=[_Row("c", 3)],
                         extractor=lambda r: (r.label, r.n))
        assert n == 1
        rows = conn.execute("SELECT label FROM t_demo").fetchall()
        assert rows == [("c",)]


def test_promote_rows_empty_input_inserts_zero():
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        n = promote_rows(conn, table="t_demo", columns=("label", "n"),
                         refresh_id="r1", rows=[],
                         extractor=lambda r: (r.label, r.n))
        assert n == 0


def test_promote_rows_preserves_other_refresh_ids():
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        promote_rows(conn, table="t_demo", columns=("label", "n"),
                     refresh_id="r1", rows=[_Row("a", 1)],
                     extractor=lambda r: (r.label, r.n))
        promote_rows(conn, table="t_demo", columns=("label", "n"),
                     refresh_id="r2", rows=[_Row("b", 2)],
                     extractor=lambda r: (r.label, r.n))
        # Re-running r1 must not touch r2 rows.
        promote_rows(conn, table="t_demo", columns=("label", "n"),
                     refresh_id="r1", rows=[_Row("a", 99)],
                     extractor=lambda r: (r.label, r.n))
        rows = conn.execute(
            "SELECT label, n, refresh_id FROM t_demo ORDER BY refresh_id, label"
        ).fetchall()
        assert rows == [("a", 99, "r1"), ("b", 2, "r2")]


def _setup_first(conn):
    """Table where refresh_id is the FIRST column (evidence_node/edge shape)."""
    conn.execute("""
        CREATE TABLE t_first (
            refresh_id VARCHAR NOT NULL,
            label VARCHAR NOT NULL,
            n INTEGER NOT NULL,
            PRIMARY KEY (refresh_id, label)
        )
    """)


def test_promote_rows_refresh_id_first_position():
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup_first(conn)
        n = promote_rows(
            conn,
            table="t_first",
            columns=("label", "n"),
            refresh_id="r1",
            rows=[_Row("a", 1), _Row("b", 2)],
            extractor=lambda r: (r.label, r.n),
            refresh_id_position="first",
        )
        assert n == 2
        rows = conn.execute(
            "SELECT refresh_id, label, n FROM t_first ORDER BY label"
        ).fetchall()
        assert rows == [("r1", "a", 1), ("r1", "b", 2)]


class _CountingConn:
    """Wraps a DuckDB connection to record per-flush batch sizes.

    Records a flush regardless of insert mechanism: executemany (container
    columns / no pandas) or the pandas DataFrame fast path (register). The test
    asserts chunked flushing, not which mechanism does the insert.
    """

    def __init__(self, conn):
        self._conn = conn
        self.flush_counts: list[int] = []

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, sql, rows):
        rows = list(rows)
        self.flush_counts.append(len(rows))
        return self._conn.executemany(sql, rows)

    def register(self, name, frame):
        self.flush_counts.append(len(frame))
        return self._conn.register(name, frame)

    def unregister(self, name):
        return self._conn.unregister(name)


def test_promote_rows_batch_size_flushes_in_chunks():
    """batch_size triggers multiple executemany flushes; row count stays exact."""
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        proxy = _CountingConn(conn)
        # 7 rows with batch_size=3 → flushes of 3, 3, 1
        n = promote_rows(
            proxy,
            table="t_demo",
            columns=("label", "n"),
            refresh_id="r1",
            rows=[_Row(f"x{i}", i) for i in range(7)],
            extractor=lambda r: (r.label, r.n),
            batch_size=3,
        )
        assert n == 7
        assert proxy.flush_counts == [3, 3, 1]
        count = conn.execute("SELECT COUNT(*) FROM t_demo").fetchone()[0]
        assert count == 7


def test_promote_rows_batch_size_exact_boundary():
    """Exactly batch_size rows → single flush, no trailing empty flush."""
    import duckdb
    with duckdb.connect(":memory:") as conn:
        _setup(conn)
        n = promote_rows(
            conn,
            table="t_demo",
            columns=("label", "n"),
            refresh_id="r1",
            rows=[_Row(f"x{i}", i) for i in range(3)],
            extractor=lambda r: (r.label, r.n),
            batch_size=3,
        )
        assert n == 3
        count = conn.execute("SELECT COUNT(*) FROM t_demo").fetchone()[0]
        assert count == 3
