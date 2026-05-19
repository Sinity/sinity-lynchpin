"""Current-state payload and HTML helpers for the ecosystem dashboard."""

from __future__ import annotations

import html
from typing import Any

from ..core.io import load_analysis_artifact


def current_state_payload() -> dict[str, Any]:
    cp_dict: dict[str, Any] = load_analysis_artifact("current_state_context_pack.json") or {}
    n_dict: dict[str, Any] = load_analysis_artifact("current_state_narrative.json") or {}
    available = bool(cp_dict)
    projects = cp_dict.get("projects", [])
    claims = cp_dict.get("claims", [])
    sections = n_dict.get("sections", [])
    salient_chains = cp_dict.get("salient_chains") or []
    salient_anomalies = cp_dict.get("salient_anomalies") or []
    readiness = cp_dict.get("readiness_forecast")
    return {
        "available": available,
        "project_count": len(projects) if isinstance(projects, list) else 0,
        "claim_count": len(claims) if isinstance(claims, list) else 0,
        "projects": [
            {"project": p.get("project", ""), "rows": len(p.get("rows", []))}
            for p in (projects if isinstance(projects, list) else [])
            if isinstance(p, dict)
        ],
        "narrative_available": bool(sections),
        "narrative_section_count": len(sections),
        "narrative_sections": [
            {
                "title": s.get("title", ""),
                "type": s.get("section_type", ""),
                "summary": (s.get("summary", "") or "")[:200],
                "score": s.get("score", 0),
            }
            for s in (sections if isinstance(sections, list) else [])
            if isinstance(s, dict)
        ],
        "temporal_signals": {
            "anomaly_count": len(salient_anomalies)
            if isinstance(salient_anomalies, list)
            else 0,
            "chain_count": len(salient_chains)
            if isinstance(salient_chains, list)
            else 0,
            "anomalies": [
                {
                    "date": a.get("date", ""),
                    "summary": (a.get("summary", "") or "")[:160],
                    "score": (a.get("payload") or {}).get("score")
                    if isinstance(a.get("payload"), dict)
                    else None,
                }
                for a in (
                    salient_anomalies if isinstance(salient_anomalies, list) else []
                )
                if isinstance(a, dict)
            ],
            "chains": [
                {
                    "date": c.get("date", ""),
                    "summary": (c.get("summary", "") or "")[:160],
                    "confidence": c.get("confidence", 0),
                }
                for c in (salient_chains if isinstance(salient_chains, list) else [])
                if isinstance(c, dict)
            ],
            "readiness": {
                "available": bool(readiness),
                "summary": (
                    readiness.get("summary", "") if isinstance(readiness, dict) else ""
                ),
                "predicted_deep_work_min": (readiness.get("payload") or {}).get(
                    "predicted_deep_work_min"
                )
                if isinstance(readiness, dict)
                and isinstance(readiness.get("payload"), dict)
                else None,
                "r_squared": (readiness.get("payload") or {}).get("r_squared")
                if isinstance(readiness, dict)
                and isinstance(readiness.get("payload"), dict)
                else None,
            },
        },
    }


def current_state_html(current_state: dict[str, Any]) -> str:
    if not current_state.get("available"):
        return (
            "<div class='panel'>"
            "<h2>Current State — Not Available</h2>"
            "<p>Context pack and narrative not yet generated. "
            "Run <code>python -m lynchpin.analysis refresh-current-state "
            "--start YYYY-MM-DD --end YYYY-MM-DD</code> to produce them.</p>"
            "</div>"
        )
    parts = [
        "<div class='grid'>",
        "<div class='panel'>",
        "<h2>Current State Overview</h2>",
        f"<p>{current_state.get('project_count', 0)} projects, "
        f"{current_state.get('claim_count', 0)} claims</p>",
        "</div>",
        "</div>",
    ]
    temporal = current_state.get("temporal_signals") or {}
    if (
        temporal.get("anomaly_count", 0)
        or temporal.get("chain_count", 0)
        or (temporal.get("readiness") or {}).get("available")
    ):
        parts.append("<div class='grid'><div class='panel'><h2>Temporal Signals</h2>")
        readiness = temporal.get("readiness") or {}
        if readiness.get("available"):
            r2 = readiness.get("r_squared")
            r2_text = f" (r²={r2:.2f})" if isinstance(r2, (int, float)) else ""
            parts.append(
                f"<div style='margin:8px 0;padding:8px;background:#f0f7ff;border-radius:4px'>"
                f"<strong>Readiness forecast</strong>{html.escape(r2_text)}: "
                f"{html.escape(readiness.get('summary', ''))}</div>"
            )
        anomalies = temporal.get("anomalies") or []
        if anomalies:
            parts.append("<h3>Anomalies</h3><ul>")
            for anomaly in anomalies:
                parts.append(
                    f"<li><strong>{html.escape(str(anomaly.get('date', '')))}</strong> — "
                    f"{html.escape(anomaly.get('summary', ''))}</li>"
                )
            parts.append("</ul>")
        chains = temporal.get("chains") or []
        if chains:
            parts.append("<h3>Causal chains</h3><ul>")
            for chain in chains:
                conf = chain.get("confidence", 0)
                conf_text = f" ({conf * 100:.0f}%)" if isinstance(conf, (int, float)) else ""
                parts.append(
                    f"<li><strong>{html.escape(str(chain.get('date', '')))}</strong> — "
                    f"{html.escape(chain.get('summary', ''))}{html.escape(conf_text)}</li>"
                )
            parts.append("</ul>")
        parts.append("</div></div>")
    if current_state.get("narrative_available"):
        parts.append("<div class='grid'><div class='panel'><h2>Narrative Sections</h2>")
        for section in current_state.get("narrative_sections", [])[:10]:
            parts.append(
                f"<div style='margin:8px 0;padding:8px;border-left:3px solid "
                f"{'#4caf50' if section.get('score', 0) >= 2 else '#ff9800'};'>"
                f"<strong>{html.escape(section.get('title', ''))}</strong> "
                f"<span style='color:#888'>({section.get('type', '')}, "
                f"score: {section.get('score', 0):.1f})</span>"
                f"<p style='margin:4px 0 0 0'>{html.escape(section.get('summary', ''))}</p>"
                f"</div>"
            )
        parts.append("</div></div>")
    return "\n".join(parts)
