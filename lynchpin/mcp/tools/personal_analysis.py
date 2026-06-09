"""MCP tools for cross-source personal analysis artifacts.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._machine_helpers import _analysis_artifact

_MISSING = {"summary": {"status": "missing"}}


@app.tool()
def anomaly_crossref_report(signal: str | None = None) -> dict[str, Any]:
    """Read the cross-source anomaly correlation report.

    When one source is anomalous, the report shows what other sources reveal
    around the same date. Optionally filter to a specific signal name.
    """
    payload = _analysis_artifact("anomaly_crossref.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    anomalies = payload.get("anomaly_days")
    if isinstance(anomalies, list):
        if signal:
            anomalies = [a for a in anomalies if isinstance(a, dict) and a.get("signal") == signal]
        result["anomaly_days"] = anomalies
        result["summary"]["anomaly_day_count"] = len(anomalies)
    cross_refs = payload.get("cross_references")
    if isinstance(cross_refs, list):
        if signal:
            cross_refs = [c for c in cross_refs if isinstance(c, dict) and c.get("signal") == signal]
        result["cross_references"] = cross_refs
    for key in ("caveats", "source_coverage", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result


@app.tool()
def life_phase_report(phase: str | None = None) -> dict[str, Any]:
    """Read the multi-signal life-phase boundary detection report.

    Returns detected phase boundaries, characterizations, and alignment with
    known events. Optionally filter to a specific phase label.
    """
    payload = _analysis_artifact("life_phase_report.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    phases = payload.get("phases")
    if isinstance(phases, list):
        if phase:
            phases = [p for p in phases if isinstance(p, dict) and p.get("label") == phase]
        result["phases"] = phases
        result["summary"]["phase_count"] = len(phases)
    boundaries = payload.get("boundaries")
    if isinstance(boundaries, list):
        result["boundaries"] = boundaries
    for key in ("known_event_alignment", "methodology", "caveats"):
        if key in payload:
            result[key] = payload[key]
    return result


@app.tool()
def productivity_predictors_report() -> dict[str, Any]:
    """Read the productivity predictors report.

    Returns a RandomForest model predicting tomorrow's deep-work hours from
    today's signals, with feature importances and diagnostics.
    """
    payload = _analysis_artifact("productivity_predictors.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    for key in ("feature_importances", "model_diagnostics", "predictions", "caveats", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result


@app.tool()
def substance_health_report(substance: str | None = None, signal: str | None = None) -> dict[str, Any]:
    """Read the substance × health lag-correlation report.

    Returns 0–7-day lag correlations between substance doses and health signals,
    dose-response curves, and abstinence period analysis.
    Optionally filter to a specific substance name or health signal.
    """
    payload = _analysis_artifact("substance_health_report.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    correlations = payload.get("lag_correlations")
    if isinstance(correlations, list):
        if substance:
            correlations = [c for c in correlations if isinstance(c, dict) and c.get("substance") == substance]
        if signal:
            correlations = [c for c in correlations if isinstance(c, dict) and c.get("signal") == signal]
        result["lag_correlations"] = correlations
        result["summary"]["correlation_count"] = len(correlations)
    for key in ("dose_response", "abstinence_periods", "caveats", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result


@app.tool()
def burnout_warning_report() -> dict[str, Any]:
    """Read the burnout-warning analysis report.

    Returns multi-signal burnout risk indicators, trend signals, and
    recovery recommendations based on HRV, stress, git activity, and sleep.
    """
    payload = _analysis_artifact("burnout_warning.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    for key in ("risk_level", "indicators", "trends", "recommendations", "caveats", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result


@app.tool()
def ai_session_efficiency_report(project: str | None = None) -> dict[str, Any]:
    """Read the AI session efficiency analysis report.

    Returns per-session and aggregate efficiency metrics for AI-assisted work:
    session duration, tool usage patterns, output quality proxies.
    Optionally filter to a specific project.
    """
    payload = _analysis_artifact("ai_session_efficiency.json")
    if payload is None:
        return _MISSING
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": "available",
        },
    }
    sessions = payload.get("sessions")
    if isinstance(sessions, list):
        if project:
            sessions = [s for s in sessions if isinstance(s, dict) and s.get("project") == project]
        result["sessions"] = sessions
        result["summary"]["session_count"] = len(sessions)
    for key in ("aggregate_metrics", "efficiency_by_project", "caveats", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result


__all__ = [
    "anomaly_crossref_report",
    "life_phase_report",
    "productivity_predictors_report",
    "substance_health_report",
    "burnout_warning_report",
    "ai_session_efficiency_report",
]
