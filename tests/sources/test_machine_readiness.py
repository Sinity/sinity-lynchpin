"""Health probes must not count history or invent samples on read failures."""
from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from lynchpin.sources import machine
from lynchpin.sources.machine_schema import BASE_METRIC_COLUMNS

NOW = datetime(2026, 10, 18, 7, tzinfo=timezone.utc)


def seed(path, *, rows=1, indexed=True):
    with sqlite3.connect(path) as conn:
        columns = ", ".join(f"{name} TEXT" + (" NOT NULL" if name in {"host", "observed_at"} else "") for name in BASE_METRIC_COLUMNS)
        conn.execute(f"CREATE TABLE metric_sample ({columns})")
        if indexed:
            conn.execute("CREATE INDEX metric_host_time ON metric_sample(host, observed_at)")
        values = ["2026-10-18T06:59:00+00:00", "neutral", *([None] * (len(BASE_METRIC_COLUMNS)-2))]
        conn.executemany(f"INSERT INTO metric_sample VALUES ({','.join('?' for _ in values)})", [values] * rows)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / "machine.sqlite"
    monkeypatch.setattr(machine, "get_config", lambda: SimpleNamespace(machine_telemetry_db=path))
    return path


def test_readiness_uses_presence_and_indexed_freshness_without_counts(configured, monkeypatch):
    seed(configured, rows=2048)
    original = machine.connect_readonly
    queries, connections = [], []
    def connect(path):
        conn = original(path)
        conn.set_trace_callback(queries.append)
        # An accidental lifetime traversal fails despite this tiny fixture.
        conn.set_progress_handler(lambda: 1, 5000)
        connections.append(conn)
        return conn
    monkeypatch.setattr(machine, "connect_readonly", connect)
    ready = machine.readiness(now=NOW)
    assert ready.status == "ready" and ready.live_rows is None
    assert ready.latest_sample_at == datetime(2026, 10, 18, 6, 59, tzinfo=timezone.utc)
    assert ready.freshness == "current"
    assert ready.table_status["metric_sample"] == "present"
    assert ready.table_status["network_sample"] == "missing"
    assert not any("COUNT(" in query.upper() for query in queries)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_legacy_without_time_index_does_not_scan_for_freshness(configured, monkeypatch):
    seed(configured, indexed=False)
    def forbidden(*args):
        raise AssertionError("unindexed freshness would scan history")
    monkeypatch.setattr(machine, "latest_observed_at", forbidden)
    ready = machine.readiness(now=NOW)
    assert ready.status == "ready" and ready.freshness == "unknown"
    assert ready.latest_sample_at is None and ready.live_rows is None


def test_empty_and_missing_are_distinct(configured):
    assert machine.readiness(now=NOW).status == "missing"
    seed(configured, rows=0)
    ready = machine.readiness(now=NOW)
    assert ready.status == "empty" and ready.live_rows == 0


@pytest.mark.parametrize("kind", ["directory", "broken-link", "broken-parent", "invalid-db"])
def test_unusable_sources_never_report_zero(configured, kind, monkeypatch):
    if kind == "directory":
        configured.mkdir()
    elif kind == "broken-link":
        configured.symlink_to("absent")
    elif kind == "broken-parent":
        parent = configured.parent / "broken"
        parent.symlink_to(configured.parent / "absent")
        path = parent / "machine.sqlite"
        monkeypatch.setattr(machine, "get_config", lambda: SimpleNamespace(machine_telemetry_db=path))
    else:
        configured.write_bytes(b"not a SQLite database")
    ready = machine.readiness(now=NOW)
    assert ready.status == "unavailable" and ready.live_rows is None


def test_schema_error_and_read_denial_are_not_empty(configured, monkeypatch):
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT)")
    ready = machine.readiness(now=NOW)
    assert ready.status == "malformed" and ready.live_rows is None
    def denied(path):
        raise PermissionError("synthetic denied")
    monkeypatch.setattr(machine, "connect_readonly", denied)
    ready = machine.readiness(now=NOW)
    assert ready.status == "unavailable" and "synthetic denied" in ready.reason


def test_stale_samples_are_present_with_explicit_freshness(configured):
    seed(configured)
    ready = machine.readiness(now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert ready.status == "ready" and ready.freshness == "stale"
    assert ready.live_rows is None


@pytest.mark.parametrize("source_status, graph_status, count", [
    ("missing", "missing", None), ("empty", "partial", 0),
    ("unavailable", "blocked", None), ("malformed", "blocked", None),
])
def test_graph_preserves_empty_missing_and_errors(configured, monkeypatch, source_status, graph_status, count):
    from lynchpin.graph import source_readiness as graph
    from lynchpin.sources.machine_models import MachineSourceReadiness
    monkeypatch.setattr(graph, "get_config", lambda: SimpleNamespace(machine_telemetry_db=configured))
    monkeypatch.setattr(graph, "_machine_promotion_staleness", lambda **kwargs: ())
    monkeypatch.setattr(machine, "readiness", lambda: MachineSourceReadiness(source_status, "neutral reason", configured, live_rows=count))
    ready = graph._machine_source()
    assert ready.status == graph_status and ready.count == count


def test_default_interface_presence_never_scans_an_unindexed_network_table(configured, monkeypatch):
    from lynchpin.sources.machine_schema import EXPECTED_NETWORK_COLUMNS
    seed(configured)
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE TABLE network_sample (" + ",".join(f"{name} TEXT" for name in EXPECTED_NETWORK_COLUMNS) + ")")
        conn.execute("INSERT INTO network_sample (interface) VALUES ('other')")
    monkeypatch.setattr(machine, "default_route_interface", lambda: "neutral0")
    ready = machine.readiness(now=NOW)
    assert ready.table_status["network_sample"] == "unavailable: default-interface presence has no index"
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE INDEX network_interface_time ON network_sample(interface, observed_at)")
    ready = machine.readiness(now=NOW)
    assert ready.table_status["network_sample"] == "empty"


@pytest.mark.parametrize("mode", ["latest", "exhausted", "early-close", "nested-close", "conversion-error"])
def test_readers_release_connections_and_cursors_without_gc(configured, monkeypatch, mode):
    import gc
    from contextlib import closing

    seed(configured, rows=2)
    with closing(sqlite3.connect(configured)) as conn:
        conn.execute("UPDATE metric_sample SET schema_version=1")
        conn.commit()
    connections, cursors = [], []

    class TrackedConnection(sqlite3.Connection):
        def execute(self, *args, **kwargs):
            cursor = super().execute(*args, **kwargs)
            cursors.append(cursor)
            return cursor

    def connect(path):
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, factory=TrackedConnection)
        connections.append(conn)
        return conn

    monkeypatch.setattr(machine, "connect_readonly", connect)
    if mode == "conversion-error":
        def fail(row):
            raise ValueError("synthetic conversion failure")
        monkeypatch.setattr(machine, "_metric_sample_from_sqlite_row", fail)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(64):
            if mode == "latest":
                assert machine.latest_metric_sample(path=configured) is not None
            elif mode == "exhausted":
                assert len(list(machine.metric_samples(path=configured))) == 2
            elif mode in {"early-close", "nested-close"}:
                iterator = machine.metric_samples() if mode == "nested-close" else machine.metric_samples(path=configured)
                assert next(iterator) is not None
                iterator.close()
            else:
                with pytest.raises(ValueError, match="synthetic conversion failure"):
                    next(machine.metric_samples(path=configured))
        for conn in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")
        for cursor in cursors:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                cursor.fetchone()
    finally:
        if was_enabled:
            gc.enable()
