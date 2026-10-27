from types import SimpleNamespace
import json
import subprocess

import pytest

from lynchpin.sources.application_health import application_health_report


def config(**overrides):
    return SimpleNamespace(**({"application_health_enabled":True,
        "application_health_timeout_seconds":1.5, "sinex_runtime_target":"/synthetic/target.json",
        "sinex_health_ca_cert":None} | overrides))


def test_native_health_interfaces_and_target(monkeypatch):
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"]==1.5 and kwargs["stdin"]==subprocess.DEVNULL
        assert kwargs["check"] is False
        payload = {"payload":{"schema_version":"sinex.runtime-health/v1","health":{"healthy":True,"serving":True}}} if argv[0]=="sinexctl" else {"archive":"/synthetic/archive","pid":42,"busy":False,"mode":{"state":"blocked","error":"synthetic outage"},"store":{"pending_inputs":[]}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(payload),stderr="")
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",run)
    rows=application_health_report(cfg=config())
    assert calls[0]==["sinexctl","--timeout","2","--format","json","--runtime-target","/synthetic/target.json","runtime","health"]
    assert calls[1]==["polylogue","status","--json"]
    assert rows[0]["target"]=="/synthetic/target.json"
    assert rows[0]["probe_status"]=="available" and rows[0]["health"]=="healthy"
    assert rows[1]["probe_status"]=="available" and rows[1]["health"]=="degraded"
    assert not any("count" in key for row in rows for key in row)


@pytest.mark.parametrize("failure",["timeout","missing","invalid","schema","failed-healthy"])
def test_failed_probes_never_become_healthy_or_zero(monkeypatch,failure):
    def run(argv, **kwargs):
        if failure=="timeout":
            raise subprocess.TimeoutExpired(argv,1.5)
        if failure=="missing":
            raise FileNotFoundError("synthetic missing executable")
        valid = {"payload":{"schema_version":"sinex.runtime-health/v1","health":{"healthy":True}}} if argv[0]=="sinexctl" else {"archive":"/synthetic/archive","pid":42,"busy":False,"mode":{"state":"standalone"},"store":{"pending_inputs":[]}}
        stdout = "invalid" if failure=="invalid" else json.dumps(valid if failure=="failed-healthy" else {"ok":True})
        return SimpleNamespace(returncode=1 if failure=="failed-healthy" else 0,stdout=stdout,stderr="")
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",run)
    rows=application_health_report(cfg=config())
    assert rows[0]["probe_status"]=="unavailable" and rows[0]["health"]=="unknown"
    assert all(row["probe_status"]=="unavailable" for row in rows)
    if failure=="timeout":
        assert all("timed out" in row["reason"] for row in rows)


def test_disabled_health_does_not_start_commands(monkeypatch):
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",lambda *a,**kw:pytest.fail("unexpected command"))
    assert application_health_report(cfg=config(application_health_enabled=False))==()


@pytest.mark.parametrize("timeout",[0,-1,float("inf"),float("nan")])
def test_timeout_configuration_is_bounded(timeout):
    with pytest.raises(ValueError,match="positive and finite"):
        application_health_report(cfg=config(application_health_timeout_seconds=timeout))


@pytest.mark.parametrize("mode,store,expected", [
    ("standalone", {"pending_inputs": []}, "healthy"),
    ("primary", {"pending_inputs": []}, "healthy"),
    ("blocked", {"pending_inputs": []}, "degraded"),
    ("standalone", {"pending_inputs": [{"hash": "synthetic"}]}, "degraded"),
    ("standalone", None, "degraded"),
])
def test_rewrite_status_separates_daemon_from_archive_health(monkeypatch, mode, store, expected):
    payload = {"archive": "/synthetic/archive", "pid": 42, "busy": True,
               "mode": {"state": mode}, "store": store}
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    row = application_health_report(cfg=config())[1]
    assert row["probe_status"] == "available" and row["health"] == expected
    assert row["details"]["daemon_available"] is True
    assert row["details"]["store_available"] is (store is not None)


def test_ca_override_keeps_tls_verification(monkeypatch):
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1,stdout="",stderr="synthetic unavailable")
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",run)
    application_health_report(cfg=config(sinex_health_ca_cert="/synthetic/ca.pem"))
    assert calls[0][calls[0].index("--ca-cert")+1]=="/synthetic/ca.pem"
    assert "--insecure" not in calls[0]


def test_real_child_timeout_is_unavailable():
    import sys
    from lynchpin.sources.application_health import _probe
    result=_probe("polylogue",[sys.executable,"-c","import time; time.sleep(2)"],0.05)
    assert result["probe_status"]=="unavailable" and result["health"]=="unknown"
    assert "timed out" in result["reason"]
