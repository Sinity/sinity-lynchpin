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
        status = "ready"
        reason = "DuckDB evidence graph builds are present"

        def to_json(self) -> dict[str, object]:
            return {"name": "evidence_graph_substrate", "status": "ready"}

    def fake_ensure_materialized(name, *, window=None, budget=None):
        calls.append((name, window, budget))
        return Result()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    payload = ensure_substrate_materialized_for_read(
        caller="test.surface",
        window=(date(2026, 5, 1), date(2026, 5, 2)),
    )

    assert calls == [(
        "evidence_graph_substrate",
        (date(2026, 5, 1), date(2026, 5, 2)),
        "inline",
    )]
    assert payload == {
        "name": "evidence_graph_substrate",
        "status": "ready",
        "caller": "test.surface",
    }


def test_ensure_substrate_materialized_for_read_logs_when_blocked(monkeypatch, caplog) -> None:
    """lynchpin-zoz: every caller discards this return value, so a blocked
    status must be observable some other way — a warning log — or a read
    over an unmaterialized window silently proceeds as if nothing were wrong.
    """
    import logging

    from lynchpin.mcp.tools._utils import ensure_substrate_materialized_for_read

    class BlockedResult:
        status = "blocked"
        reason = "no transparent materializer is defined for this contract"

        def to_json(self) -> dict[str, object]:
            return {"name": "evidence_graph_substrate", "status": "blocked"}

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget=None: BlockedResult(),
    )

    with caplog.at_level(logging.WARNING, logger="lynchpin.mcp.tools._utils"):
        ensure_substrate_materialized_for_read(
            caller="test.blocked_surface",
            window=(date(2020, 1, 1), date(2020, 1, 5)),
        )

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "test.blocked_surface" in message
    assert "status=blocked" in message


def test_ensure_substrate_materialized_for_read_does_not_log_when_ready(monkeypatch, caplog) -> None:
    import logging

    from lynchpin.mcp.tools._utils import ensure_substrate_materialized_for_read

    class ReadyResult:
        status = "ready"
        reason = "DuckDB evidence graph builds are present"

        def to_json(self) -> dict[str, object]:
            return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget=None: ReadyResult(),
    )

    with caplog.at_level(logging.WARNING, logger="lynchpin.mcp.tools._utils"):
        ensure_substrate_materialized_for_read(caller="test.ready_surface", window=None)

    assert caplog.records == []


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
