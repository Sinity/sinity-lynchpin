"""Tests for conversation fork detection (M.15)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.composite.conversation_forks import (
    detect_fork_chains,
    render_fork_chains,
)
from lynchpin.sources.polylogue import ConversationLineage, SessionProfile

UTC = timezone.utc


def _lineage(*, conv_id: str, parent: str | None = None, branch: str | None = None,
            title: str = "", provider: str = "claude-code") -> ConversationLineage:
    return ConversationLineage(
        conversation_id=conv_id,
        parent_conversation_id=parent,
        branch_type=branch,
        provider=provider,
        title=title,
        created_at=datetime(2026, 5, 7, 12, tzinfo=UTC),
    )


def _profile(*, conv_id: str, message_count: int) -> SessionProfile:
    return SessionProfile(
        conversation_id=conv_id,
        provider="claude-code",
        title="x",
        message_count=message_count,
        word_count=message_count * 100,
        first_message_at=datetime(2026, 5, 7, 12, tzinfo=UTC),
        last_message_at=datetime(2026, 5, 7, 13, tzinfo=UTC),
        engaged_duration_ms=60_000,
        wall_duration_ms=3_600_000,
        work_event_kind="implementation",
        work_event_projects=(),
        total_cost_usd=0.0,
        canonical_session_date=date(2026, 5, 7),
        tool_use_count=0,
        thinking_count=0,
        auto_tags=(),
    )


def test_substantial_fork_creates_chain():
    """A fork that itself crossed the substantial threshold should surface."""
    lineages = [
        _lineage(conv_id="parent"),
        _lineage(conv_id="fork1", parent="parent", branch="fork", title="alt approach"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=20),
        _profile(conv_id="fork1", message_count=15),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    assert len(chains) == 1
    assert chains[0].parent_id == "parent"
    assert chains[0].fork_count == 1
    assert chains[0].children[0]["conversation_id"] == "fork1"


def test_multi_explore_creates_chain_even_when_individual_forks_small():
    """Substantial parent + multiple meaningful children → multi-explore signal."""
    lineages = [
        _lineage(conv_id="parent"),
        _lineage(conv_id="fork1", parent="parent", branch="fork"),
        _lineage(conv_id="fork2", parent="parent", branch="sidechain"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=15),
        _profile(conv_id="fork1", message_count=3),  # small but ≥2 children
        _profile(conv_id="fork2", message_count=2),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    assert len(chains) == 1
    assert chains[0].fork_count == 1
    assert chains[0].sidechain_count == 1


def test_subagent_branches_dont_surface():
    """Subagent children are routine for Claude Code; don't pollute the surface."""
    lineages = [
        _lineage(conv_id="parent"),
        _lineage(conv_id="agent1", parent="parent", branch="subagent"),
        _lineage(conv_id="agent2", parent="parent", branch="subagent"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=20),
        _profile(conv_id="agent1", message_count=15),
        _profile(conv_id="agent2", message_count=12),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    assert chains == []


def test_continuation_branches_dont_surface():
    """Continuations resume the same trajectory — not divergence."""
    lineages = [
        _lineage(conv_id="parent"),
        _lineage(conv_id="cont1", parent="parent", branch="continuation"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=20),
        _profile(conv_id="cont1", message_count=20),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    assert chains == []


def test_substantial_threshold_filters_tiny_forks():
    """A short parent with one tiny fork doesn't qualify."""
    lineages = [
        _lineage(conv_id="parent"),
        _lineage(conv_id="fork1", parent="parent", branch="fork"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=3),
        _profile(conv_id="fork1", message_count=2),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    assert chains == []


def test_render_includes_parent_and_fork_counts():
    lineages = [
        _lineage(conv_id="parent", title="big project"),
        _lineage(conv_id="fork1", parent="parent", branch="fork"),
    ]
    profiles = [
        _profile(conv_id="parent", message_count=20),
        _profile(conv_id="fork1", message_count=15),
    ]
    chains = detect_fork_chains(lineages=lineages, profiles=profiles)
    rendered = render_fork_chains(chains)
    assert "big project" in rendered
    assert "1 substantial fork chains" in rendered


def test_render_empty_when_no_chains():
    rendered = render_fork_chains([])
    assert "No substantial conversation forks" in rendered
