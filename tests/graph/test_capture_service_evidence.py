from datetime import datetime,timedelta,timezone
import json
import sqlite3
from types import SimpleNamespace

from lynchpin.graph.capture_service_evidence import capture_service_evidence


def fixture(tmp_path, *, indexed=True):
    inventory=tmp_path/"inventory.json"
    inventory.write_text(json.dumps({"schema":"sinnix-runtime-inventory-v2","hostname":"fixture-host","slices":{},"units":{},"observedServices":[],"mounts":[],"captures":[
        {"name":"system-capture","producer":{"surface":"fixture","unit":"shared.service","manager":"system"}},
        {"name":"user-capture","producer":{"surface":"fixture","unit":"shared.service","manager":"user"}},
        {"name":"standalone","producer":None},
    ]}))
    db=tmp_path/"telemetry.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE service_state(observed_at TEXT NOT NULL,host TEXT,boot_id TEXT,unit TEXT NOT NULL,scope TEXT,active_state TEXT,sub_state TEXT,main_pid INTEGER,control_group TEXT,memory_current_bytes INTEGER,cpu_usage_nsec INTEGER,io_read_bytes INTEGER,io_write_bytes INTEGER)")
        if indexed:
            conn.execute("CREATE INDEX unit_time ON service_state(unit,observed_at)")
        for scope,active,sub in [("system","failed","failed"),("user","active","running")]:
            conn.execute("INSERT INTO service_state VALUES (?,?,?,?,?,?,?,0,'',0,0,0,0)",['2026-06-05T23:59:55+00:00','fixture-host',None,'shared.service',scope,active,sub])
    return SimpleNamespace(sinnix_runtime_inventory_json=inventory,machine_telemetry_db=db)


def test_declared_manager_and_host_bindings_preserve_distinct_service_evidence(tmp_path):
    cfg=fixture(tmp_path)
    start=datetime(2026,6,6,tzinfo=timezone.utc)
    report=capture_service_evidence(start=start,end=start+timedelta(seconds=20),cfg=cfg)
    assert report['status']=='available'
    rows={row['name']:row for row in report['captures']}
    assert rows['system-capture']['inactive_seconds']==20
    assert rows['system-capture']['unobserved_seconds']==0
    assert rows['user-capture']['inactive_seconds']==0
    assert rows['user-capture']['unobserved_seconds']==0
    assert rows['standalone']['status']=='unbound'
    assert 'inactive_seconds' not in rows['standalone']


def test_unindexed_read_is_unavailable_not_measured_zero(tmp_path):
    cfg=fixture(tmp_path,indexed=False)
    start=datetime(2026,6,6,tzinfo=timezone.utc)
    report=capture_service_evidence(start=start,end=start+timedelta(seconds=20),cfg=cfg)
    assert report['status']=='partial'
    assert report['captures'][0]['inactive_seconds'] is None
    assert 'indexed' in report['captures'][0]['reason']


def test_missing_inventory_is_explicitly_unavailable(tmp_path):
    cfg=SimpleNamespace(sinnix_runtime_inventory_json=tmp_path/'missing.json',machine_telemetry_db=tmp_path/'missing.sqlite')
    start=datetime(2026,6,6,tzinfo=timezone.utc)
    report=capture_service_evidence(start=start,end=start+timedelta(seconds=20),cfg=cfg)
    assert report['status']=='unavailable' and report['captures']==[]
    assert not cfg.machine_telemetry_db.exists()


def test_cli_context_and_mcp_use_identical_service_product(monkeypatch,tmp_path):
    from datetime import date
    from lynchpin.graph import coverage,source_readiness
    from lynchpin.core.evidence import SourceReadinessReport
    from lynchpin.mcp.tools import substrate
    cfg=fixture(tmp_path)
    monkeypatch.setattr(coverage,"get_config",lambda:cfg)
    monkeypatch.setattr("lynchpin.materialization.audit_materialization",lambda **kw:[])
    report=coverage.coverage_report(start=date(2026,6,6),end=date(2026,6,7),repair_materializations=False)
    monkeypatch.setattr("lynchpin.graph.capture_service_evidence.get_config",lambda:cfg)
    monkeypatch.setattr(substrate,"contract_coverage",lambda **kw:[])
    monkeypatch.setattr(substrate,"substrate_readiness_report",lambda:{"sources":[],"summary":{"trustworthy":True}})
    monkeypatch.setattr("lynchpin.graph.source_readiness._machine_promotion_staleness",lambda **kw:())
    monkeypatch.setattr("lynchpin.sources.application_health.application_health_report",lambda:())
    mcp=substrate.analysis_readiness(start="2026-06-06",end="2026-06-06")
    assert mcp['capture_service_evidence']==report.service_evidence
    context=SourceReadinessReport(date(2026,6,6),date(2026,6,6),datetime.now(timezone.utc),(),service_evidence=report.service_evidence)
    assert "system-capture" in coverage.render_coverage_report(report)
    assert "system-capture" in source_readiness.render_source_readiness(context)


def test_budget_failure_does_not_return_partial_measurements_as_zero(tmp_path):
    from lynchpin.graph.capture_service_evidence import _read_service
    cfg=fixture(tmp_path)
    start=datetime(2026,6,6,tzinfo=timezone.utc)
    with sqlite3.connect(cfg.machine_telemetry_db) as conn:
        row=_read_service(conn,host='fixture-host',unit='shared.service',scope='system',start=start,end=start+timedelta(seconds=20),deadline=-1)
    assert row['status']=='unavailable' and row['inactive_seconds'] is None
    assert row['unobserved_seconds'] is None


def test_service_evidence_closes_connection_on_success_and_failure(tmp_path, monkeypatch):
    import importlib
    import pytest
    module = importlib.import_module("lynchpin.graph.capture_service_evidence")
    cfg = fixture(tmp_path)
    original = module.connect_readonly
    connections = []
    def connect(path):
        conn = original(path)
        connections.append(conn)
        return conn
    monkeypatch.setattr(module, "connect_readonly", connect)
    start = datetime(2026, 6, 6, tzinfo=timezone.utc)
    assert module.capture_service_evidence(start=start, end=start+timedelta(seconds=20), cfg=cfg)["status"] == "available"
    def fail(conn):
        raise sqlite3.OperationalError("synthetic schema failure")
    monkeypatch.setattr(module, "validate_service_state_schema", fail)
    assert module.capture_service_evidence(start=start, end=start+timedelta(seconds=20), cfg=cfg)["status"] == "unavailable"
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
