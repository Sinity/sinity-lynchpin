"""Tests for composite.period_synthesis."""

from __future__ import annotations

from datetime import date, datetime

from lynchpin.graph.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
)
from lynchpin.graph.period_synthesis import (
    ROLE_ARC_OPENER,
    ROLE_CRISIS,
    ROLE_RECOVERY,
    ROLE_STEADY,
    build_period_synthesis,
)


def _node(
    *,
    id: str,
    kind: str,
    on: date,
    project: str | None = None,
    summary: str = "",
    payload: dict | None = None,
) -> EvidenceNode:
    return EvidenceNode(
        id=id,
        kind=kind,  # type: ignore[arg-type]
        source="test",
        date=on,
        project=project,
        summary=summary or kind,
        payload=payload,
    )


def _empty_graph(start: date, end: date, nodes: list[EvidenceNode]) -> EvidenceGraph:
    return EvidenceGraph(
        start=start,
        end=end,
        generated_at=datetime(2026, 5, 7, 12),
        mode="local-fast",
        nodes=tuple(nodes),
        edges=(),
        caveats=(),
    )


def test_month_synthesis_descends_to_weeks() -> None:
    nodes = [
        _node(id=f"c:{i}", kind="commit", on=date(2026, 5, d))
        for i, d in enumerate([2, 5, 9, 12, 16, 19, 23, 26, 30])
    ]
    graph = _empty_graph(date(2026, 5, 1), date(2026, 5, 31), nodes)
    synth = build_period_synthesis(scale="month", key="2026-05", graph=graph)

    assert synth is not None
    assert synth.period.scale == "month"
    assert synth.children
    assert all(c.period.scale == "week" for c in synth.children)
    assert synth.rollup.node_count == 9


def test_quarter_synthesis_descends_to_months_and_weeks() -> None:
    nodes = [_node(id="c:1", kind="commit", on=date(2026, 5, 15))]
    graph = _empty_graph(date(2026, 4, 1), date(2026, 6, 30), nodes)
    synth = build_period_synthesis(scale="quarter", key="2026-Q2", graph=graph)

    assert synth is not None
    assert synth.period.scale == "quarter"
    months = synth.children
    assert {m.period.scale for m in months} == {"month"}
    assert any(m.children for m in months)


def test_health_arc_falling_when_sleep_drops() -> None:
    nodes = [
        _node(
            id=f"sq:{d}",
            kind="sleep_quality",
            on=date(2026, 5, d),
            payload={"sleep_score": 90.0 - d, "sleep_hours": 7.0},
        )
        for d in range(1, 16)
    ]
    graph = _empty_graph(date(2026, 5, 1), date(2026, 5, 31), nodes)
    synth = build_period_synthesis(scale="month", key="2026-05", graph=graph, max_depth=0)

    assert synth is not None
    assert synth.health_arc is not None
    assert synth.health_arc.sleep_score_trend == "falling"
    assert synth.health_arc.n_days == 15


def test_role_assignment_uses_anomaly_density() -> None:
    # Week 1 of May 2026 (Apr 27 - May 3): one anomaly, score 5
    # Week 2 (May 4 - May 10): no anomalies (steady)
    # Week 3 (May 11 - May 17): big anomaly cluster (crisis)
    # Week 4 (May 18 - May 24): low (recovery)
    # Week 5 (May 25 - May 31): low (steady)
    nodes = [
        _node(
            id="a:opener",
            kind="temporal_anomaly",
            on=date(2026, 5, 1),
            payload={"score": 5.0},
        ),
        _node(
            id="a:crisis1",
            kind="temporal_anomaly",
            on=date(2026, 5, 12),
            payload={"score": 10.0},
        ),
        _node(
            id="a:crisis2",
            kind="temporal_anomaly",
            on=date(2026, 5, 14),
            payload={"score": 8.0},
        ),
    ]
    graph = _empty_graph(date(2026, 5, 1), date(2026, 5, 31), nodes)
    synth = build_period_synthesis(scale="month", key="2026-05", graph=graph)

    assert synth is not None
    roles = [c.role_in_parent for c in synth.children]
    assert ROLE_ARC_OPENER in roles
    assert ROLE_CRISIS in roles
    assert ROLE_RECOVERY in roles or ROLE_STEADY in roles


def test_empty_period_emits_caveat() -> None:
    graph = _empty_graph(date(2026, 5, 1), date(2026, 5, 31), [])
    synth = build_period_synthesis(scale="month", key="2026-05", graph=graph, max_depth=0)
    assert synth is not None
    assert "no evidence nodes within this period" in synth.caveats


def test_invalid_period_returns_none() -> None:
    graph = _empty_graph(date(2026, 5, 1), date(2026, 5, 31), [])
    assert build_period_synthesis(scale="month", key="not-a-key", graph=graph) is None
