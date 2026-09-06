"""Endpoint diagnostics measure the selected overlay, not historical constants."""

import json

import duckdb
import pytest

from lynchpin.substrate.graph import load_evidence_graph
from lynchpin.substrate.integrity import measure_graph_integrity


@pytest.fixture
def conn():
    db = duckdb.connect(":memory:")
    db.execute("""
        CREATE TABLE evidence_graph_build (
            refresh_id VARCHAR PRIMARY KEY, start_date DATE DEFAULT '2026-01-01',
            end_date DATE DEFAULT '2026-01-31', mode VARCHAR DEFAULT 'materialized',
            generated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, caveats JSON DEFAULT '[]',
            predecessor_refresh_id VARCHAR, predecessor_tail_start DATE,
            projects VARCHAR[] DEFAULT []
        );
        CREATE TABLE evidence_node (
            refresh_id VARCHAR, id VARCHAR, kind VARCHAR DEFAULT 'raw_log',
            source VARCHAR DEFAULT 'fixture', date DATE, project VARCHAR,
            summary VARCHAR DEFAULT '', start_ts TIMESTAMPTZ, end_ts TIMESTAMPTZ,
            url VARCHAR, payload JSON, provenance JSON, caveats JSON DEFAULT '[]'
        );
        CREATE TABLE evidence_edge (
            refresh_id VARCHAR, source_id VARCHAR, target_id VARCHAR,
            relation VARCHAR DEFAULT 'co_occurs', evidence VARCHAR DEFAULT '',
            weight DOUBLE DEFAULT 1.0
        )
    """)
    yield db
    db.close()


def build(conn, rid, parent=None, cutoff=None):
    conn.execute(
        "INSERT INTO evidence_graph_build (refresh_id, predecessor_refresh_id, predecessor_tail_start) VALUES (?, ?, ?)",
        [rid, parent, cutoff],
    )


def node(conn, rid, key, day="2026-01-01"):
    conn.execute("INSERT INTO evidence_node (refresh_id, id, date) VALUES (?, ?, ?)", [rid, key, day])


def edge(conn, rid, source, target):
    conn.execute("INSERT INTO evidence_edge (refresh_id, source_id, target_id) VALUES (?, ?, ?)", [rid, source, target])


def assert_matches_reader(conn, rid):
    measured = measure_graph_integrity(conn, rid)
    graph = load_evidence_graph(conn, refresh_id=rid)
    assert graph is not None
    assert graph.refresh_id == rid
    assert graph.graph_integrity == measured
    return measured, graph


def test_healthy_graph_has_no_fabricated_caveat(conn):
    build(conn, "base")
    node(conn, "base", "a")
    node(conn, "base", "b")
    edge(conn, "base", "a", "b")
    result, graph = assert_matches_reader(conn, "base")
    assert result["status"] == "available"
    assert result["node_count"] == 2
    assert result["total_edges"] == 1
    assert result["orphaned_edges"] == 0
    assert result["orphan_ratio"] == 0.0
    assert not graph.caveats


def test_actual_orphans_count_each_edge_once(conn):
    build(conn, "base")
    node(conn, "base", "a")
    edge(conn, "base", "a", "missing")
    edge(conn, "base", "ghost", "a")
    edge(conn, "base", "ghost", "missing")
    result, graph = assert_matches_reader(conn, "base")
    assert result["status"] == "partial"
    assert result["total_edges"] == 3
    assert result["missing_source"] == 2
    assert result["missing_target"] == 2
    assert result["orphaned_edges"] == 3
    assert len(graph.caveats) == 1


def test_incremental_edges_resolve_into_retained_prefix(conn):
    build(conn, "base")
    node(conn, "base", "a")
    node(conn, "base", "b", "2026-01-02")
    node(conn, "base", "old-tail", "2026-01-10")
    edge(conn, "base", "a", "b")
    edge(conn, "base", "a", "old-tail")
    build(conn, "child", "base", "2026-01-10")
    node(conn, "child", "new-tail", "2026-01-12")
    edge(conn, "child", "new-tail", "a")
    result, _ = assert_matches_reader(conn, "child")
    assert result["node_count"] == 3
    assert result["total_edges"] == 2
    assert result["orphaned_edges"] == 0


def test_replaced_node_shadows_older_edges(conn):
    build(conn, "base")
    node(conn, "base", "a")
    node(conn, "base", "b")
    edge(conn, "base", "a", "b")
    build(conn, "child", "base", "2026-01-10")
    node(conn, "child", "a", "2026-01-12")
    result, _ = assert_matches_reader(conn, "child")
    assert result["node_count"] == 2
    assert result["total_edges"] == 0
    assert result["orphaned_edges"] == 0


def test_deleted_tail_cannot_rescue_new_dangling_edge(conn):
    build(conn, "base")
    node(conn, "base", "old-tail", "2026-01-10")
    build(conn, "child", "base", "2026-01-10")
    node(conn, "child", "new-tail", "2026-01-12")
    edge(conn, "child", "new-tail", "old-tail")
    result, _ = assert_matches_reader(conn, "child")
    assert result["node_count"] == 1
    assert result["total_edges"] == 1
    assert result["missing_target"] == 1
    assert result["orphaned_edges"] == 1


def test_unrelated_generation_does_not_resolve_endpoint(conn):
    build(conn, "chosen")
    node(conn, "chosen", "a")
    edge(conn, "chosen", "a", "elsewhere")
    build(conn, "unrelated")
    node(conn, "unrelated", "elsewhere")
    result, _ = assert_matches_reader(conn, "chosen")
    assert result["orphaned_edges"] == 1


@pytest.mark.parametrize("rid", [None, "absent"])
def test_no_build_is_unknown_not_measured_healthy_or_corrupt(conn, rid):
    result = measure_graph_integrity(conn, rid)
    assert result["status"] == "missing"
    assert result["measured"] is False
    assert result["total_edges"] is None
    assert result["orphan_ratio"] is None


def test_actual_build_caveats_are_preserved(conn):
    build(conn, "base")
    caveat = {"source": "fixture", "status": "partial", "message": "Known capture gap"}
    conn.execute("UPDATE evidence_graph_build SET caveats = ?", [json.dumps([caveat])])
    _, graph = assert_matches_reader(conn, "base")
    assert [c.message for c in graph.caveats] == ["Known capture gap"]
