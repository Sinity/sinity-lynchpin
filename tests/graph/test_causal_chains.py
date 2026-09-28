"""Tests for causal_chains, particularly the Arc C.1 templates over the
ai_work_event substrate (Arc A) and the file-overlap / kind-tier
constraints introduced in this revision.
"""

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


def test_ai_work_event_to_commit_requires_file_overlap():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    we = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "implementation", "kind_tier": "high",
                 "file_paths": ["src/foo.py"], "tools_used": ["Edit"]},
    )
    commit_match = _node(
        node_id="c1", kind="commit", start=base + timedelta(hours=1),
        payload={"paths": ("src/foo.py",), "conventional_kind": "feat"},
    )
    chains = detect_chains([we, commit_match])
    we_to_commit = [c for c in chains if c.chain_type == "ai_work_event_to_commit"]
    assert len(we_to_commit) == 1
    assert we_to_commit[0].confidence > 0.0


def test_ai_work_event_to_commit_skipped_when_files_dont_overlap():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    we = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "implementation", "kind_tier": "high",
                 "file_paths": ["src/foo.py"]},
    )
    commit_other = _node(
        node_id="c2", kind="commit", start=base + timedelta(hours=1),
        payload={"paths": ("src/bar.py",), "conventional_kind": "feat"},
    )
    chains = detect_chains([we, commit_other])
    assert not any(c.chain_type == "ai_work_event_to_commit" for c in chains)


def test_ai_research_to_impl_to_commit_requires_kind_sequence():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    research = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "research", "kind_tier": "medium", "file_paths": ["docs/notes.md"]},
    )
    impl = _node(
        node_id="we2", kind="ai_work_event", start=base + timedelta(hours=2),
        payload={"kind": "implementation", "kind_tier": "high", "file_paths": ["src/foo.py"]},
    )
    commit = _node(
        node_id="c1", kind="commit", start=base + timedelta(hours=4),
        payload={"paths": ("src/foo.py",), "conventional_kind": "feat"},
    )
    chains = detect_chains([research, impl, commit])
    matched = [c for c in chains if c.chain_type == "ai_research_to_impl_to_commit"]
    assert len(matched) == 1


def test_ai_research_to_impl_to_commit_low_tier_filters_out():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    research_low = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "research", "kind_tier": "low", "file_paths": ["docs/notes.md"]},
    )
    impl_low = _node(
        node_id="we2", kind="ai_work_event", start=base + timedelta(hours=2),
        payload={"kind": "implementation", "kind_tier": "low", "file_paths": ["src/foo.py"]},
    )
    commit = _node(
        node_id="c1", kind="commit", start=base + timedelta(hours=4),
        payload={"paths": ("src/foo.py",), "conventional_kind": "feat"},
    )
    chains = detect_chains([research_low, impl_low, commit])
    # Tier floor is `medium`; low events should be excluded.
    assert not any(c.chain_type == "ai_research_to_impl_to_commit" for c in chains)


def test_ai_debug_to_build_fix_to_fix_chain():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    debug = _node(
        node_id="we1", kind="ai_work_event", start=base,
        payload={"kind": "debugging", "kind_tier": "high", "tools_used": ["Bash"]},
    )
    pattern = _node(
        node_id="tp1", kind="terminal_pattern", start=base + timedelta(minutes=20),
        payload={"kind": "build_fix_loop"},
    )
    fix_commit = _node(
        node_id="c1", kind="commit", start=base + timedelta(minutes=60),
        payload={"paths": ("src/foo.py",), "conventional_kind": "fix"},
    )
    chains = detect_chains([debug, pattern, fix_commit])
    matched = [c for c in chains if c.chain_type == "ai_debug_to_build_fix_to_fix"]
    assert len(matched) == 1
    assert matched[0].node_kinds == ("ai_work_event", "terminal_pattern", "commit")


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
    session = _node(node_id="session", kind="ai_session", start=base)
    commit = _node(node_id="commit", kind="commit", start=base + timedelta(minutes=20))
    unrelated = _node(node_id="other", kind="terminal_session", start=base + timedelta(minutes=10))
    original = [c for c in detect_chains([session, commit]) if c.chain_type == "ai_assisted_implementation"]
    inserted = [c for c in detect_chains([session, unrelated, commit]) if c.chain_type == "ai_assisted_implementation"]
    assert len(original) == len(inserted) == 1
    assert original[0].id == inserted[0].id
    assert not detect_chains([session, commit], max_gap_minutes=10)


def test_date_only_event_cannot_supply_temporal_relation():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    session = EvidenceNode(id="date-only", kind="ai_session", source="test", date=base.date(),
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


def test_same_filename_in_other_project_is_not_overlap():
    base = datetime(2026, 5, 7, 12, tzinfo=UTC)
    event = _node(node_id="event", kind="ai_work_event", start=base,
                  project="project-a", payload={"file_paths": ["src/main.py"]})
    commit = _node(node_id="commit", kind="commit", start=base + timedelta(minutes=10),
                   project="project-b", payload={"paths": ["src/main.py"]})
    assert not any(c.chain_type == "ai_work_event_to_commit" for c in detect_chains([event, commit]))
