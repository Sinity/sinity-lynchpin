"""System-signal source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode, EvidenceNodeKind


def add_health(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    from .health_bridge import (
        build_health_evidence,
        build_sleep_evidence,
        build_sleep_productivity_links,
    )

    for sq in build_sleep_evidence(start=start, end=end):
        nodes.append(
            EvidenceNode(
                id=sq.id,
                kind="sleep_quality",
                source="sleep",
                date=sq.date,
                project=None,
                summary=sq.summary,
                payload=sq.payload,
                provenance=EvidenceProvenance("sleep", "local-heavy"),
            )
        )

    for hm in build_health_evidence(start=start, end=end):
        nodes.append(
            EvidenceNode(
                id=hm.id,
                kind="health_metric",
                source="health",
                date=hm.date,
                project=None,
                summary=hm.summary,
                payload=hm.payload,
                provenance=EvidenceProvenance("health", "local-heavy"),
            )
        )

    for link in build_sleep_productivity_links(start=start, end=end):
        nodes.append(
            EvidenceNode(
                id=link.id,
                kind="sleep_quality",
                source="sleep",
                date=link.sleep_date,
                project=None,
                summary=link.summary,
                payload=link.payload,
                provenance=EvidenceProvenance("sleep", "local-heavy"),
            )
        )


def add_readiness(nodes: list[EvidenceNode], *, end: date) -> None:
    """Build a forecast for the day after ``end`` and emit it as a graph node.

    Failures and degraded fits surface as a ``readiness_forecast`` node with
    ``status="unavailable"`` so the consumer always sees source-readiness
    context, never a silent gap.
    """
    from .readiness import build_readiness_forecast, readiness_payload

    target = end + timedelta(days=1)
    try:
        result = build_readiness_forecast(target_date=target)
    except Exception as exc:  # numpy/scipy import failure or data corruption
        nodes.append(
            EvidenceNode(
                id=f"readiness:{target.isoformat()}:error",
                kind="readiness_forecast",
                source="readiness",
                date=target,
                project=None,
                summary=f"readiness forecast unavailable ({type(exc).__name__})",
                payload={"status": "error", "reason": str(exc)[:200]},
                provenance=EvidenceProvenance("readiness", "local-fast"),
            )
        )
        return

    payload = readiness_payload(result)
    if payload["status"] == "available":
        summary = (
            f"forecast: {payload['predicted_deep_work_min']:.0f} min deep work on "
            f"{target.isoformat()} (95% CI {payload['ci_low']:.0f}-{payload['ci_high']:.0f}, "
            f"r2={payload['r_squared']:.2f}, n={payload['sample_n']})"
        )
    else:
        summary = f"readiness forecast {payload['status']}: {payload.get('reason', '')}"

    nodes.append(
        EvidenceNode(
            id=f"readiness:{target.isoformat()}:{payload['status']}",
            kind="readiness_forecast",
            source="readiness",
            date=target,
            project=None,
            summary=summary,
            payload=payload,
            provenance=EvidenceProvenance("readiness", "local-fast"),
        )
    )


def add_temporal_signals(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    from .temporal_signals import detect_temporal_signals

    kind_map: dict[str, EvidenceNodeKind] = {
        "temporal_changepoint": "temporal_changepoint",
        "temporal_trend": "temporal_trend",
        "temporal_anomaly": "temporal_anomaly",
        "temporal_rhythm": "temporal_rhythm",
    }
    for idx, event in enumerate(detect_temporal_signals(start=start, end=end)):
        node_kind = kind_map.get(event.kind)
        if node_kind is None:
            continue
        nodes.append(
            EvidenceNode(
                id=f"temporal:{event.kind}:{event.signal}:{event.event_date.isoformat()}:{idx}",
                kind=node_kind,
                source="temporal",
                date=event.event_date,
                project=None,
                summary=event.summary,
                payload=event.payload,
                provenance=EvidenceProvenance("temporal", "local-fast"),
            )
        )
