from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from lynchpin import materialization


def _graph_fixture_connection(
    *,
    start: date,
    end: date,
    input_fingerprint: str | None,
    with_input_fingerprint: bool = True,
):
    import duckdb

    conn = duckdb.connect()
    input_fingerprint_column = ", input_fingerprint VARCHAR" if with_input_fingerprint else ""
    conn.execute(
        f"""
        CREATE TABLE evidence_graph_build (
            refresh_id VARCHAR,
            start_date DATE,
            end_date DATE,
            projects VARCHAR[],
            materialized_at TIMESTAMP,
            generated_at TIMESTAMP
            {input_fingerprint_column}
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE substrate_promotion_run (
            refresh_id VARCHAR,
            status VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMP
        )
        """
    )
    if with_input_fingerprint:
        conn.execute(
            """
            INSERT INTO evidence_graph_build
                (refresh_id, start_date, end_date, projects, materialized_at,
                 generated_at, input_fingerprint)
            VALUES ('base', ?, ?, [], TIMESTAMP '2026-09-01 00:00:00',
                    TIMESTAMP '2026-09-01 00:00:00', ?)
            """,
            [start, end, input_fingerprint],
        )
    else:
        conn.execute(
            """
            INSERT INTO evidence_graph_build
                (refresh_id, start_date, end_date, projects, materialized_at,
                 generated_at)
            VALUES ('base', ?, ?, [], TIMESTAMP '2026-09-01 00:00:00',
                    TIMESTAMP '2026-09-01 00:00:00')
            """,
            [start, end],
        )
    conn.execute("INSERT INTO substrate_promotion_run VALUES ('base', 'ok')")
    conn.execute(
        """
        INSERT INTO substrate_source_status VALUES
            ('base', 'evidence_graph', 'ok', TIMESTAMP '2026-09-01 00:00:00')
        """
    )
    return conn


def _patch_serving_generation(monkeypatch, conn):
    class Generation:
        connection = conn
        database_path = "fixture"

    @contextmanager
    def serving():
        yield Generation()

    monkeypatch.setattr("lynchpin.substrate.connection.serving_generation", serving)


def _incremental_graph_fixture():
    import duckdb

    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE evidence_graph_build (
            refresh_id VARCHAR, start_date DATE, end_date DATE,
            projects VARCHAR[], materialized_at TIMESTAMP,
            generated_at TIMESTAMP, input_fingerprint VARCHAR,
            predecessor_refresh_id VARCHAR, predecessor_tail_start DATE
        )
        """
    )
    conn.execute(
        "CREATE TABLE substrate_promotion_run (refresh_id VARCHAR, status VARCHAR)"
    )
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR, source VARCHAR, status VARCHAR, recorded_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO evidence_graph_build VALUES
        ('base', DATE '2011-01-30', DATE '2026-09-08', [],
         TIMESTAMP '2026-09-07', TIMESTAMP '2026-09-07', 'old', NULL, NULL),
        ('tail', DATE '2011-01-30', DATE '2026-09-08', [],
         TIMESTAMP '2026-09-08', TIMESTAMP '2026-09-08', 'new', 'base', DATE '2026-09-01')
        """
    )
    conn.execute("INSERT INTO substrate_promotion_run VALUES ('base', 'ok'), ('tail', 'degraded')")
    conn.execute(
        "INSERT INTO substrate_source_status VALUES "
        "('base', 'evidence_graph', 'ok', TIMESTAMP '2026-09-07'), "
        "('tail', 'evidence_graph', 'ok', TIMESTAMP '2026-09-08')"
    )
    return conn


def test_incremental_graph_fingerprint_checks_visible_historical_predecessor(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 24), date(2026, 8, 25))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def test_incremental_graph_fingerprint_checks_both_sides_of_tail_boundary(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 31), date(2026, 9, 2))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def test_incremental_graph_fingerprint_skips_current_tail(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 9, 2), date(2026, 9, 3))
        )
    finally:
        conn.close()
    assert plan.action == "skip"


def test_incremental_graph_fingerprint_rejects_missing_visible_predecessor(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    conn.execute(
        "UPDATE evidence_graph_build SET predecessor_refresh_id = 'missing' WHERE refresh_id = 'tail'"
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 24), date(2026, 8, 25))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def test_incremental_graph_fingerprint_rejects_visible_predecessor_cycle(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    conn.execute(
        "UPDATE evidence_graph_build "
        "SET predecessor_refresh_id = 'tail', predecessor_tail_start = DATE '2026-09-01' "
        "WHERE refresh_id = 'base'"
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 24), date(2026, 8, 25))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def test_incremental_graph_fingerprint_rejects_tail_without_predecessor(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    conn.execute(
        "UPDATE evidence_graph_build "
        "SET predecessor_refresh_id = NULL, predecessor_tail_start = DATE '2026-09-01' "
        "WHERE refresh_id = 'tail'"
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 24), date(2026, 8, 25))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def test_incremental_graph_fingerprint_rejects_malformed_ancestor_tail(monkeypatch) -> None:
    conn = _incremental_graph_fixture()
    conn.execute(
        "UPDATE evidence_graph_build "
        "SET predecessor_refresh_id = NULL, predecessor_tail_start = DATE '2026-08-20', "
        "input_fingerprint = 'new' "
        "WHERE refresh_id = 'base'"
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(
            window=(date(2026, 8, 24), date(2026, 8, 25))
        )
    finally:
        conn.close()
    assert plan.action == "converge"


def _result(name: str, *, changed: bool = False) -> materialization.MaterializationResult:
    return materialization.MaterializationResult(
        name=name,
        status="updated" if changed else "ready",
        changed=changed,
        reason="fixture",
        elapsed_ms=1,
        product_paths=(),
        source_high_water={},
        coverage={},
    )


def _row(name: str, *, last: date | None, status: str = "ready") -> materialization.MaterializedDataset:
    return materialization.MaterializedDataset(
        name=name,
        status=status,  # type: ignore[arg-type]
        authority="synthetic fixture",
        query_surface="synthetic fixture",
        materialized_paths=(Path(f"/tmp/{name}.json"),),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 8, 1) if last else None,
        last_date=last,
        materialization_hint="fixture",
        reason="fixture",
    )


def test_warm_read_is_a_noop_after_the_first_durable_check(monkeypatch) -> None:
    calls = 0

    def ensure(_name: str, **_kwargs):
        nonlocal calls
        calls += 1
        return _result("activitywatch")

    materialization._READ_CONVERGENCE_CACHE.clear()
    monkeypatch.setattr(materialization, "_ensure_materialized_typed", ensure)
    window = (date(2026, 8, 1), date(2026, 8, 3))

    first = materialization.ensure_materialized("activitywatch", window=window)
    second = materialization.ensure_materialized("activitywatch", window=window)

    assert first.status == second.status == "ready"
    assert first.changed is second.changed is False
    assert calls == 1


def test_missing_tail_uses_only_the_bounded_tail(monkeypatch) -> None:
    before = _row("activitywatch", last=date(2026, 8, 3))
    monkeypatch.setattr(materialization, "_audit_one", lambda *_args, **_kwargs: before)

    plan = materialization.plan_read_convergence(
        product="activitywatch",
        window=(date(2026, 8, 1), date(2026, 8, 6)),
    )

    assert plan.action == "converge"
    assert plan.effective_window == (date(2026, 8, 3), date(2026, 8, 6))


def test_graph_read_uses_broad_predecessor_with_bounded_tail(monkeypatch) -> None:
    predecessor_start = date(2011, 1, 30)
    predecessor_end = date(2026, 9, 30)
    requested = (date(2026, 9, 6), date(2026, 9, 8))
    conn = _graph_fixture_connection(
        start=predecessor_start,
        end=predecessor_end,
        input_fingerprint="old",
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "new")
    try:
        plan = materialization.plan_read_convergence(window=requested)
    finally:
        conn.close()

    assert plan.action == "converge"
    assert plan.predecessor_refresh_id == "base"
    assert plan.effective_window == (predecessor_start, predecessor_end)
    assert plan.tail_start == requested[0]
    assert requested[1] - plan.tail_start <= timedelta(days=materialization.READ_CONVERGENCE_MAX_DAYS)


def test_schema45_graph_read_treats_missing_fingerprint_as_stale(monkeypatch) -> None:
    requested = (date(2026, 9, 6), date(2026, 9, 8))
    conn = _graph_fixture_connection(
        start=date(2011, 1, 30),
        end=date(2026, 9, 30),
        input_fingerprint=None,
        with_input_fingerprint=False,
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "current")
    try:
        plan = materialization.plan_read_convergence(window=requested)
    finally:
        conn.close()

    assert plan.action == "converge"
    assert plan.predecessor_refresh_id == "base"
    assert plan.effective_window == (date(2011, 1, 30), date(2026, 9, 30))


def test_graph_fingerprint_tracks_direct_source_file_revisions(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.sources import sms

    sms_root = tmp_path / "SMS"
    sms_root.mkdir()
    source_file = sms_root / "SMS_export.csv"
    source_file.write_text("initial", encoding="utf-8")
    monkeypatch.setattr(sms, "SMS_ROOT", sms_root)

    cfg = materialization.get_config()
    first = {
        row["name"]: row
        for row in materialization._graph_source_revisions(cfg)
    }
    source_file.write_text("changed", encoding="utf-8")
    second = {
        row["name"]: row
        for row in materialization._graph_source_revisions(cfg)
    }

    assert {"git_live", "sms", "outlook", "svn", "gmail", "google_takeout_files", "analysis_artifacts_files"} <= first.keys()
    assert first["sms"] != second["sms"]


def test_graph_build_without_fingerprint_is_stale(monkeypatch) -> None:
    requested = (date(2026, 9, 6), date(2026, 9, 8))
    conn = _graph_fixture_connection(
        start=requested[0], end=requested[1], input_fingerprint=None
    )
    _patch_serving_generation(monkeypatch, conn)
    monkeypatch.setattr(materialization, "_substrate_fingerprint", lambda *_args: "current")
    try:
        plan = materialization.plan_read_convergence(window=requested)
    finally:
        conn.close()

    assert plan.action == "converge"
    assert plan.predecessor_refresh_id == "base"
    assert plan.tail_start == requested[1] - timedelta(days=materialization.READ_CONVERGENCE_OVERLAP_DAYS)


def test_identical_concurrent_reads_single_flight_the_materializer(monkeypatch) -> None:
    calls = 0
    lock = threading.Lock()
    def ensure(name: str, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _result(name, changed=True)

    materialization._READ_CONVERGENCE_CACHE.clear()
    monkeypatch.setattr(materialization, "_ensure_materialized_typed", ensure)
    window = (date(2026, 8, 1), date(2026, 8, 6))
    results = []

    def read() -> None:
        results.append(materialization.ensure_materialized("activitywatch_event_index", window=window))

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(results) == 4
    assert all(result.status == "updated" for result in results)


def test_pinned_read_metadata_never_enters_convergence(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("pinned reads must not converge")

    monkeypatch.setattr(materialization, "ensure_materialized", fail)
    from lynchpin.mcp.tools._utils import pinned_materialization_for_read

    payload = pinned_materialization_for_read(caller="test.pinned", refresh_id="historical-1")

    assert payload == {
        "name": "evidence_graph_substrate",
        "status": "pinned",
        "changed": False,
        "caller": "test.pinned",
        "refresh_id": "historical-1",
    }
