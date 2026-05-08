"""Tests for multi-layer caveat propagation (M.16)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lynchpin.composite.causal_chains import detect_chains
from lynchpin.composite.evidence import (
    EvidenceCaveat,
    caveat_summary,
    degrade_confidence,
    propagate_caveats,
)
from lynchpin.composite.evidence_graph import EvidenceNode

UTC = timezone.utc


def _caveat(source: str, status: str, message: str) -> EvidenceCaveat:
    return EvidenceCaveat(source=source, status=status, message=message)


def test_propagate_caveats_dedups_across_sources():
    a = (_caveat("polylogue", "partial", "stub"),)
    b = (_caveat("polylogue", "partial", "stub"), _caveat("git", "stale", "60d"))
    out = propagate_caveats(a, b)
    assert len(out) == 2
    assert {(c.source, c.status) for c in out} == {("polylogue", "partial"), ("git", "stale")}


def test_propagate_caveats_preserves_order_first_seen():
    a = (_caveat("git", "stale", "old"),)
    b = (_caveat("polylogue", "partial", "x"),)
    out = propagate_caveats(a, b)
    assert out[0].source == "git"
    assert out[1].source == "polylogue"


def test_degrade_confidence_no_caveats_returns_base():
    assert degrade_confidence(0.85, ()) == 0.85


def test_degrade_confidence_partial_caveat_dampens():
    # 0.85 × 0.90 = 0.765
    out = degrade_confidence(0.85, (_caveat("polylogue", "partial", "x"),))
    assert abs(out - 0.765) < 1e-6


def test_degrade_confidence_compounds_across_layers():
    caveats = (
        _caveat("polylogue", "partial", "x"),
        _caveat("git", "stale", "old"),
        _caveat("activitywatch", "partial", "y"),
    )
    # 0.85 × 0.90 × 0.85 × 0.90 = 0.585...
    out = degrade_confidence(0.85, caveats)
    assert abs(out - 0.85 * 0.90 * 0.85 * 0.90) < 1e-6


def test_degrade_confidence_clamps_at_floor():
    caveats = tuple(_caveat(f"src{i}", "missing", "x") for i in range(8))
    out = degrade_confidence(0.85, caveats, floor=0.10)
    assert out == 0.10


def test_caveat_summary_counts_distinct_statuses():
    caveats = (
        _caveat("polylogue", "partial", "a"),
        _caveat("git", "partial", "b"),
        _caveat("polylogue", "stale", "c"),
    )
    summary = caveat_summary(caveats)
    assert summary == {"partial": 2, "stale": 1}


def _node(*, node_id: str, kind: str, start: datetime, project: str = "demo",
          payload: dict | None = None, caveats: tuple = ()) -> EvidenceNode:
    return EvidenceNode(
        id=node_id, kind=kind, source="test",  # type: ignore[arg-type]
        date=start.date(), project=project, start=start, end=start + timedelta(minutes=10),
        summary="x", payload=payload or {}, caveats=caveats,
    )


def test_chain_carries_caveats_from_all_nodes():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    we = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "implementation", "kind_tier": "high",
                 "file_paths": ["src/foo.py"]},
        caveats=(_caveat("polylogue", "partial", "Work-event labels heuristic"),),
    )
    commit = _node(
        node_id="c1", kind="commit", start=base + timedelta(hours=1),
        payload={"paths": ("src/foo.py",), "conventional_kind": "feat"},
        caveats=(_caveat("git", "partial", "co-authored-by trailer is incomplete"),),
    )
    chains = detect_chains([we, commit])
    we_to_commit = [c for c in chains if c.chain_type == "ai_work_event_to_commit"]
    assert len(we_to_commit) == 1
    chain = we_to_commit[0]
    sources = {c.source for c in chain.caveats}
    assert "polylogue" in sources
    assert "git" in sources


def test_chain_confidence_degrades_when_nodes_have_caveats():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    # Two chains: one with caveats on both nodes, one without.
    we_clean = _node(
        node_id="we_clean", kind="ai_work_event", start=base,
        payload={"kind": "implementation", "kind_tier": "high",
                 "file_paths": ["src/clean.py"]},
    )
    commit_clean = _node(
        node_id="c_clean", kind="commit", start=base + timedelta(hours=1),
        payload={"paths": ("src/clean.py",), "conventional_kind": "feat"},
    )
    we_dirty = _node(
        node_id="we_dirty", kind="ai_work_event", start=base + timedelta(hours=2),
        payload={"kind": "implementation", "kind_tier": "high",
                 "file_paths": ["src/dirty.py"]},
        caveats=(_caveat("polylogue", "partial", "x"),),
    )
    commit_dirty = _node(
        node_id="c_dirty", kind="commit", start=base + timedelta(hours=3),
        payload={"paths": ("src/dirty.py",), "conventional_kind": "feat"},
        caveats=(_caveat("git", "stale", "old"),),
    )
    chains = detect_chains([we_clean, commit_clean, we_dirty, commit_dirty])
    by_id = {c.id: c for c in chains if c.chain_type == "ai_work_event_to_commit"}
    clean_chain = next(c for c in by_id.values() if "we_clean" in c.node_ids)
    dirty_chain = next(c for c in by_id.values() if "we_dirty" in c.node_ids)
    # Clean chain: no caveats → confidence == base
    assert clean_chain.payload["caveat_count"] == 0
    assert clean_chain.confidence == clean_chain.payload["base_confidence"]
    # Dirty chain: 2 caveats → confidence < base
    assert dirty_chain.payload["caveat_count"] == 2
    assert dirty_chain.confidence < dirty_chain.payload["base_confidence"]
