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
