"""MCP tools for cross-source personal analysis artifacts.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.core.io import load_materialized_analysis_artifact


def _read_report(name: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep artifact availability distinct from its analysis and provenance.

    A readable historical report is not necessarily a current or successful
    analysis. Preserve its declared status and the materializer's evidence;
    never infer freshness or absence of limitations from a file's existence.
    """
    payload, materialization = load_materialized_analysis_artifact(name)
    if not isinstance(payload, dict):
        return {"summary": {"status": "missing"}, "materialization": materialization}, None
    declared_status = payload.get("status")
    if not isinstance(declared_status, str):
        declared_summary = payload.get("summary")
        declared_status = (
            declared_summary.get("status") if isinstance(declared_summary, dict) else None
        )
    result: dict[str, Any] = {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "status": declared_status if isinstance(declared_status, str) else "available",
            "artifact_available": True,
        },
        "materialization": materialization,
    }
    for key in ("caveats", "source_coverage", "signal_coverage", "methodology"):
        if key in payload:
            result[key] = payload[key]
    return result, payload


def anomaly_crossref_report(signal: str | None = None) -> dict[str, Any]:
    """Read the cross-source anomaly report, optionally filtering a signal."""
    result, payload = _read_report("anomaly_crossref.json")
    if payload is None:
        return result
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
    return result


def life_phase_report(phase: str | None = None) -> dict[str, Any]:
    """Read phase boundaries, coverage, and event annotations from a report.

    Known-event annotations are not themselves independently detected changes.
    Optionally filter characterizations to a specific phase label.
    """
    result, payload = _read_report("life_phase_report.json")
    if payload is None:
        return result
    phases = payload.get("phases")
    if isinstance(phases, list):
        if phase:
            phases = [p for p in phases if isinstance(p, dict) and p.get("label") == phase]
        result["phases"] = phases
        result["summary"]["phase_count"] = len(phases)
    boundaries = payload.get("boundaries")
    if isinstance(boundaries, list):
        result["boundaries"] = boundaries
    # Preserve both the current writer's contract and historical artifacts.
    for key in ("event_annotations", "known_event_alignment"):
        if key in payload:
            result[key] = payload[key]
    return result


def productivity_predictors_report() -> dict[str, Any]:
    """Read recorded productivity predictions, diagnostics, and limitations."""
    result, payload = _read_report("productivity_predictors.json")
    if payload is None:
        return result
    for key in ("feature_importances", "model_diagnostics", "predictions"):
        if key in payload:
            result[key] = payload[key]
    return result


def substance_health_report(substance: str | None = None, signal: str | None = None) -> dict[str, Any]:
    """Read recorded dose/health associations and their coverage caveats.

    Missing dose entries do not by themselves establish abstinence.
    Optionally filter a substance name or health signal.
    """
    result, payload = _read_report("substance_health_report.json")
    if payload is None:
        return result
    correlations = payload.get("lag_correlations")
    if isinstance(correlations, list):
        if substance:
            correlations = [c for c in correlations if isinstance(c, dict) and c.get("substance") == substance]
        if signal:
            correlations = [c for c in correlations if isinstance(c, dict) and c.get("signal") == signal]
        result["lag_correlations"] = correlations
        result["summary"]["correlation_count"] = len(correlations)
    for key in ("dose_response", "abstinence_periods"):
        if key in payload:
            result[key] = payload[key]
    return result


def burnout_warning_report() -> dict[str, Any]:
    """Read the recorded burnout indicators, not a fresh clinical assessment."""
    result, payload = _read_report("burnout_warning.json")
    if payload is None:
        return result
    for key in ("risk_level", "indicators", "trends", "recommendations"):
        if key in payload:
            result[key] = payload[key]
    return result


def ai_session_efficiency_report(project: str | None = None) -> dict[str, Any]:
    """Read session output proxies and their recorded provenance/limitations."""
    result, payload = _read_report("ai_session_efficiency.json")
    if payload is None:
        return result
    sessions = payload.get("sessions")
    if isinstance(sessions, list):
        if project:
            sessions = [s for s in sessions if isinstance(s, dict) and s.get("project") == project]
        result["sessions"] = sessions
        result["summary"]["session_count"] = len(sessions)
    for key in ("aggregate_metrics", "efficiency_by_project"):
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
