from types import SimpleNamespace
import json
import subprocess

import pytest

from lynchpin.sources.application_health import application_health_report

STATUS = {"commit_seq": 14425078, "writer": True, "search_index": {"position": 1, "behind": 3}}


def config(**overrides):
    return SimpleNamespace(**({"application_health_enabled": True,
        "application_health_timeout_seconds": 1.5} | overrides))


def test_status_command_reports_health(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 1.5 and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=0, stdout=json.dumps(STATUS), stderr="")
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run", run)
    rows = application_health_report(cfg=config())
    assert calls == [["sinexctl", "--json", "status"]]
    assert rows[0]["probe_status"] == "available" and rows[0]["health"] == "healthy"
    assert rows[0]["details"] == {"commit_seq": 14425078, "writer": True, "search_index_behind": 3}


def test_stopped_writer_is_degraded(monkeypatch):
    stopped = STATUS | {"writer": False}
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run",
                        lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(stopped), stderr=""))
    assert application_health_report(cfg=config())[0]["health"] == "degraded"


@pytest.mark.parametrize("failure", ["timeout", "missing", "invalid", "no-position", "failed-exit"])
def test_failed_probes_never_become_healthy_or_zero(monkeypatch, failure):
    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1.5)
        if failure == "missing":
            raise FileNotFoundError("synthetic missing executable")
        stdout = {"invalid": "invalid", "no-position": json.dumps({"writer": True})}.get(failure, json.dumps(STATUS))
        return SimpleNamespace(returncode=1 if failure == "failed-exit" else 0, stdout=stdout, stderr="")
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run", run)
    rows = application_health_report(cfg=config())
    assert rows[0]["probe_status"] == "unavailable" and rows[0]["health"] == "unknown"
    if failure == "timeout":
        assert "timed out" in rows[0]["reason"]


def test_disabled_health_does_not_start_commands(monkeypatch):
    monkeypatch.setattr("lynchpin.sources.application_health.subprocess.run", lambda *a, **kw: pytest.fail("unexpected command"))
    assert application_health_report(cfg=config(application_health_enabled=False)) == ()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_configuration_is_bounded(timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        application_health_report(cfg=config(application_health_timeout_seconds=timeout))


def test_real_child_timeout_is_unavailable():
    import sys
    from lynchpin.sources.application_health import _probe
    result = _probe("sinex", [sys.executable, "-c", "import time; time.sleep(2)"], 0.05)
    assert result["probe_status"] == "unavailable" and result["health"] == "unknown"
    assert "timed out" in result["reason"]
