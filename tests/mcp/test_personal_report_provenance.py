"""Report readers preserve evidence quality instead of laundering it."""

import pytest

from lynchpin.mcp.tools import personal_analysis as reports


READERS = (
    reports.anomaly_crossref_report,
    reports.life_phase_report,
    reports.productivity_predictors_report,
    reports.substance_health_report,
    reports.burnout_warning_report,
    reports.ai_session_efficiency_report,
)


@pytest.mark.parametrize("reader", READERS)
def test_reader_keeps_materialization_and_coverage(monkeypatch, reader):
    materialization = {"status": "blocked", "reason": "fixture source unavailable"}
    payload = {
        "window_start": "2020-01-01",
        "window_end": "2020-01-31",
        "generated_at_utc": "2020-02-01T00:00:00Z",
        "signal_coverage": ["fixture signal: no observations"],
        "source_coverage": {"fixture": "missing"},
        "caveats": ["absence is not zero"],
    }
    monkeypatch.setattr(reports, "load_materialized_analysis_artifact", lambda name: (payload, materialization))
    result = reader()
    assert result["materialization"] == materialization
    assert result["signal_coverage"] == payload["signal_coverage"]
    assert result["source_coverage"] == payload["source_coverage"]
    assert result["caveats"] == payload["caveats"]
    assert result["summary"]["window_end"] == "2020-01-31"
    assert result["summary"]["artifact_available"] is True


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("status_payload", [{"status": "insufficient_data"}, {"summary": {"status": "failed"}}])
def test_reader_does_not_replace_declared_analysis_status(monkeypatch, reader, status_payload):
    monkeypatch.setattr(reports, "load_materialized_analysis_artifact", lambda name: (status_payload, {}))
    result = reader()
    assert result["summary"]["status"] in {"insufficient_data", "failed"}
    assert result["summary"]["artifact_available"] is True


@pytest.mark.parametrize("reader", READERS)
def test_missing_report_keeps_failure_evidence(monkeypatch, reader):
    evidence = {"requested_artifact_status": "malformed", "reason": "invalid fixture"}
    monkeypatch.setattr(reports, "load_materialized_analysis_artifact", lambda name: (None, evidence))
    result = reader()
    assert result["summary"]["status"] == "missing"
    assert result["materialization"] == evidence


def test_life_phase_keeps_current_event_annotations(monkeypatch):
    payload = {"event_annotations": [{"label": "fixture", "aligned": False}], "phases": []}
    monkeypatch.setattr(reports, "load_materialized_analysis_artifact", lambda name: (payload, {}))
    assert reports.life_phase_report()["event_annotations"] == payload["event_annotations"]
