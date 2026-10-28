"""Temporal chains over terminal, focus and commit evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lynchpin.graph.causal_chains import detect_chains
from lynchpin.core.evidence_graph import EvidenceNode

UTC = timezone.utc


def _node(
    *,
    node_id: str,
    kind: str,
    start: datetime,
    project: str = "demo",
    payload: dict | None = None,
    summary: str = "",
) -> EvidenceNode:
    return EvidenceNode(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        source="test",
        date=start.date(),
        project=project,
        start=start,
        end=start + timedelta(minutes=10),
        summary=summary,
        payload=payload or {},
    )








def test_terminal_fix_test_template_still_fires():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    pattern = _node(
        node_id="tp1", kind="terminal_pattern", start=base,
        payload={"kind": "build_fix_loop"},
    )
    fix = _node(
        node_id="c1", kind="commit", start=base + timedelta(minutes=10),
        payload={"conventional_kind": "fix"},
    )
    test_commit = _node(
        node_id="c2", kind="commit", start=base + timedelta(minutes=30),
        payload={"conventional_kind": "test"},
    )
    chains = detect_chains([pattern, fix, test_commit])
    assert any(c.chain_type == "terminal_fix_test" for c in chains)


def test_unrelated_observation_preserves_support_identity_and_bound():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    session = _node(node_id="session", kind="deep_work_block", start=base)
    commit = _node(node_id="commit", kind="commit", start=base + timedelta(minutes=20))
    unrelated = _node(node_id="other", kind="terminal_session", start=base + timedelta(minutes=10))
    original = [c for c in detect_chains([session, commit]) if c.chain_type == "deep_work_delivery"]
    inserted = [c for c in detect_chains([session, unrelated, commit]) if c.chain_type == "deep_work_delivery"]
    assert len(original) == len(inserted) == 1
    assert original[0].id == inserted[0].id
    assert not detect_chains([session, commit], max_gap_minutes=10)


def test_date_only_event_cannot_supply_temporal_relation():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    session = EvidenceNode(id="date-only", kind="deep_work_block", source="test", date=base.date(),
                           project="demo", summary="date only")
    commit = _node(node_id="commit", kind="commit", start=base)
    assert not detect_chains([session, commit])


def test_error_burst_label_uses_measured_counts():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    first = _node(node_id="first", kind="terminal_session", start=base, payload={"error_count": 0})
    second = _node(node_id="second", kind="terminal_session", start=base + timedelta(minutes=10),
                   payload={"error_count": 0})
    commit = _node(node_id="commit", kind="commit", start=base + timedelta(minutes=20))
    assert not any(c.chain_type == "error_burst_resolution" for c in detect_chains([first, second, commit]))
    first = _node(node_id="first", kind="terminal_session", start=base, payload={"error_count": 3})
    matched = [c for c in detect_chains([first, second, commit]) if c.chain_type == "error_burst_resolution"]
    assert len(matched) == 1 and "3 → 0" in matched[0].summary
    from lynchpin.graph.narrative import _chain_section
    assert "3 → 0" in _chain_section(matched[0]).title


