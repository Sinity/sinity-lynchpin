from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import reload_config


def _analysis_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "generated" / "analysis"
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(root))
    reload_config(monkeypatch)
    root.mkdir(parents=True)
    return root


def _write_artifact(root: Path, name: str, payload: dict) -> None:
    (root / name).write_text(json.dumps(payload))


_BASE_PAYLOAD = {
    "generated_at_utc": "2026-06-01T00:00:00+00:00",
    "window_start": "2026-05-01",
    "window_end": "2026-05-31",
}


def test_anomaly_crossref_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import anomaly_crossref_report

    result = anomaly_crossref_report()
    assert result["summary"]["status"] == "missing"


def test_anomaly_crossref_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "anomaly_crossref.json", {
        **_BASE_PAYLOAD,
        "anomaly_days": [
            {"date": "2026-05-10", "signal": "hrv", "z_score": 2.5},
            {"date": "2026-05-15", "signal": "git_commits", "z_score": -1.8},
        ],
        "cross_references": [{"date": "2026-05-10", "signal": "hrv", "other_source": "stress"}],
        "caveats": ["limited data"],
    })
    from lynchpin.mcp.tools.personal_analysis import anomaly_crossref_report

    result = anomaly_crossref_report()
    assert result["summary"]["status"] == "available"
    assert result["summary"]["anomaly_day_count"] == 2
    assert len(result["anomaly_days"]) == 2
    assert result["caveats"] == ["limited data"]


def test_anomaly_crossref_signal_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "anomaly_crossref.json", {
        **_BASE_PAYLOAD,
        "anomaly_days": [
            {"date": "2026-05-10", "signal": "hrv", "z_score": 2.5},
            {"date": "2026-05-15", "signal": "git_commits", "z_score": -1.8},
        ],
    })
    from lynchpin.mcp.tools.personal_analysis import anomaly_crossref_report

    result = anomaly_crossref_report(signal="hrv")
    assert len(result["anomaly_days"]) == 1
    assert result["anomaly_days"][0]["signal"] == "hrv"


def test_life_phase_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import life_phase_report

    result = life_phase_report()
    assert result["summary"]["status"] == "missing"


def test_life_phase_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "life_phase_report.json", {
        **_BASE_PAYLOAD,
        "phases": [
            {"label": "high-output", "start": "2026-05-01", "end": "2026-05-15"},
            {"label": "recovery", "start": "2026-05-16", "end": "2026-05-31"},
        ],
        "boundaries": [{"date": "2026-05-16", "confidence": 0.85}],
    })
    from lynchpin.mcp.tools.personal_analysis import life_phase_report

    result = life_phase_report()
    assert result["summary"]["status"] == "available"
    assert result["summary"]["phase_count"] == 2
    assert len(result["boundaries"]) == 1


def test_life_phase_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "life_phase_report.json", {
        **_BASE_PAYLOAD,
        "phases": [
            {"label": "high-output", "start": "2026-05-01", "end": "2026-05-15"},
            {"label": "recovery", "start": "2026-05-16", "end": "2026-05-31"},
        ],
    })
    from lynchpin.mcp.tools.personal_analysis import life_phase_report

    result = life_phase_report(phase="recovery")
    assert len(result["phases"]) == 1
    assert result["phases"][0]["label"] == "recovery"


def test_productivity_predictors_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import productivity_predictors_report

    result = productivity_predictors_report()
    assert result["summary"]["status"] == "missing"


def test_productivity_predictors_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "productivity_predictors.json", {
        **_BASE_PAYLOAD,
        "feature_importances": [{"feature": "hrv_rmssd", "importance": 0.32}],
        "model_diagnostics": {"r2": 0.71, "mae": 0.8},
        "caveats": ["small n"],
    })
    from lynchpin.mcp.tools.personal_analysis import productivity_predictors_report

    result = productivity_predictors_report()
    assert result["summary"]["status"] == "available"
    assert len(result["feature_importances"]) == 1
    assert result["model_diagnostics"]["r2"] == 0.71


def test_substance_health_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import substance_health_report

    result = substance_health_report()
    assert result["summary"]["status"] == "missing"


def test_substance_health_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "substance_health_report.json", {
        **_BASE_PAYLOAD,
        "lag_correlations": [
            {"substance": "test_substance", "signal": "hrv_rmssd", "lag_days": 1, "r": 0.4},
            {"substance": "test_substance_2", "signal": "stress_mean", "lag_days": 0, "r": -0.3},
        ],
        "caveats": ["n=40"],
    })
    from lynchpin.mcp.tools.personal_analysis import substance_health_report

    result = substance_health_report()
    assert result["summary"]["status"] == "available"
    assert result["summary"]["correlation_count"] == 2


def test_substance_health_filter_by_substance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "substance_health_report.json", {
        **_BASE_PAYLOAD,
        "lag_correlations": [
            {"substance": "test_substance", "signal": "hrv_rmssd", "lag_days": 1, "r": 0.4},
            {"substance": "test_substance_2", "signal": "stress_mean", "lag_days": 0, "r": -0.3},
        ],
    })
    from lynchpin.mcp.tools.personal_analysis import substance_health_report

    result = substance_health_report(substance="test_substance")
    assert len(result["lag_correlations"]) == 1
    assert result["lag_correlations"][0]["substance"] == "test_substance"


def test_burnout_warning_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import burnout_warning_report

    result = burnout_warning_report()
    assert result["summary"]["status"] == "missing"


def test_burnout_warning_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "burnout_warning.json", {
        **_BASE_PAYLOAD,
        "risk_level": "moderate",
        "indicators": [{"name": "hrv_trend", "direction": "declining", "severity": "moderate"}],
        "recommendations": ["take a rest day"],
    })
    from lynchpin.mcp.tools.personal_analysis import burnout_warning_report

    result = burnout_warning_report()
    assert result["summary"]["status"] == "available"
    assert result["risk_level"] == "moderate"
    assert len(result["indicators"]) == 1


def test_ai_session_efficiency_missing_returns_missing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analysis_root(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.personal_analysis import ai_session_efficiency_report

    result = ai_session_efficiency_report()
    assert result["summary"]["status"] == "missing"


def test_ai_session_efficiency_returns_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "ai_session_efficiency.json", {
        **_BASE_PAYLOAD,
        "sessions": [
            {"project": "lynchpin", "duration_min": 45, "tool_calls": 12},
            {"project": "sinex", "duration_min": 30, "tool_calls": 8},
        ],
        "aggregate_metrics": {"avg_duration_min": 37.5},
    })
    from lynchpin.mcp.tools.personal_analysis import ai_session_efficiency_report

    result = ai_session_efficiency_report()
    assert result["summary"]["status"] == "available"
    assert result["summary"]["session_count"] == 2
    assert result["aggregate_metrics"]["avg_duration_min"] == 37.5


def test_ai_session_efficiency_project_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _analysis_root(tmp_path, monkeypatch)
    _write_artifact(root, "ai_session_efficiency.json", {
        **_BASE_PAYLOAD,
        "sessions": [
            {"project": "lynchpin", "duration_min": 45, "tool_calls": 12},
            {"project": "sinex", "duration_min": 30, "tool_calls": 8},
        ],
    })
    from lynchpin.mcp.tools.personal_analysis import ai_session_efficiency_report

    result = ai_session_efficiency_report(project="sinex")
    assert result["summary"]["session_count"] == 1
    assert result["sessions"][0]["project"] == "sinex"
