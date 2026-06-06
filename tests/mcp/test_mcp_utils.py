from __future__ import annotations

from datetime import date

import pytest


def test_best_materialized_refresh_id_rejects_non_identifier_table_name() -> None:
    from lynchpin.mcp.tools._utils import best_materialized_refresh_id

    class Conn:
        def execute(self, _sql: str):
            raise AssertionError("invalid table names must not reach SQL")

    with pytest.raises(ValueError, match="invalid substrate table identifier"):
        best_materialized_refresh_id(
            Conn(),
            "commit_fact; DROP TABLE commit_fact",
            caller="test.invalid",
        )


def test_best_materialized_refresh_id_is_read_only(tmp_path) -> None:
    import duckdb

    from lynchpin.mcp.tools._utils import best_materialized_refresh_id

    db_path = tmp_path / "substrate.duckdb"
    ledger = tmp_path / "freshness.sqlite"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            refresh_id VARCHAR,
            materialized_at TIMESTAMPTZ
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMPTZ
        )
        """
    )
    conn.execute("INSERT INTO commit_fact VALUES ('a', 'r1', now())")
    conn.execute("INSERT INTO substrate_source_status VALUES ('r1', 'commits', 'ok', now())")

    refresh_id = best_materialized_refresh_id(
        conn,
        "commit_fact",
        caller="test.mcp",
        ledger_path=ledger,
    )

    assert refresh_id == "r1"
    assert not ledger.exists()


def test_latest_materialized_refresh_id_is_read_only(tmp_path) -> None:
    import duckdb

    from lynchpin.mcp.tools._utils import latest_materialized_refresh_id

    db_path = tmp_path / "substrate.duckdb"
    ledger = tmp_path / "freshness.sqlite"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMPTZ
        )
        """
    )
    conn.execute("INSERT INTO substrate_source_status VALUES ('r-old', 'commits', 'ok', now() - INTERVAL 1 DAY)")
    conn.execute("INSERT INTO substrate_source_status VALUES ('r-new', 'commits', 'ok', now())")

    refresh_id = latest_materialized_refresh_id(conn, caller="test.latest", ledger_path=ledger)

    assert refresh_id == "r-new"
    assert not ledger.exists()


def test_ensure_substrate_materialized_for_read_reports_caller(monkeypatch) -> None:
    from lynchpin.mcp.tools._utils import ensure_substrate_materialized_for_read

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "evidence_graph_substrate", "status": "ready"}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    payload = ensure_substrate_materialized_for_read(
        caller="test.surface",
        window=(date(2026, 5, 1), date(2026, 5, 2)),
    )

    assert calls == [(
        "evidence_graph_substrate",
        (date(2026, 5, 1), date(2026, 5, 2)),
    )]
    assert payload == {
        "name": "evidence_graph_substrate",
        "status": "ready",
        "caller": "test.surface",
    }


def test_require_best_materialized_refresh_id_is_read_only_when_blocked(tmp_path) -> None:
    import duckdb

    from lynchpin.mcp.tools._utils import require_best_materialized_refresh_id

    db_path = tmp_path / "substrate.duckdb"
    ledger = tmp_path / "freshness.sqlite"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            refresh_id VARCHAR,
            materialized_at TIMESTAMPTZ
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMPTZ
        )
        """
    )

    with pytest.raises(RuntimeError, match="requires substrate table"):
        require_best_materialized_refresh_id(
            conn,
            "commit_fact",
            caller="test.required",
            tool="test_required",
            ledger_path=ledger,
        )

    assert not ledger.exists()
