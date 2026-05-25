"""Conversation fork detection (M.15).

Polylogue's branch model is flat in Lynchpin's view by default. This module
surfaces the divergence cases — forks and sidechains — as evidence
because they often signal "I tried approach X, branched off to try Y."

A fork chain is a parent conversation plus its descendants, where the
descendants are linked via ``parent_conversation_id``. We surface chains
where:

  - the parent is "substantial" (≥10 messages) AND has multiple children
    (parallel exploration), OR
  - any single fork reaches its own substantial size (≥10 messages),
    suggesting the side path was meaningful work, not just an aborted
    one-shot

`subagent` branches are tracked separately because their volume is
different in kind (Claude Code agent spawns are routine, not divergence).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence

from ..sources.polylogue import (
    ConversationLineage,
    SessionProfile,
    conversation_lineages,
    iter_session_profiles,
)


@dataclass(frozen=True)
class ConversationForkChain:
    """One parent conversation and the substantive forks that branched off it."""
    parent_id: str
    parent_provider: str
    parent_title: str
    parent_created_at: Optional[datetime]
    parent_message_count: int
    fork_count: int
    sidechain_count: int
    subagent_count: int
    children: tuple[dict, ...]
    significance_reason: str


_SUBSTANTIAL_MESSAGE_COUNT = 10


def detect_fork_chains(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    lineages: Optional[Sequence[ConversationLineage]] = None,
    profiles: Optional[Sequence[SessionProfile]] = None,
) -> list[ConversationForkChain]:
    """Walk the lineage graph; return chains worth surfacing as evidence.

    ``lineages`` and ``profiles`` accept caller-supplied data for tests; when
    omitted, both are loaded from the local Polylogue archive.
    """
    if lineages is None:
        lineages = conversation_lineages(start=start, end=end)
    lineages = tuple(lineages)
    if profiles is None:
        # Graceful-degrade: missing/incomplete polylogue insights yield an
        # empty profile set rather than crashing fork-chain detection.
        from ..sources.polylogue import PolylogueMaterializationError
        try:
            profiles = tuple(iter_session_profiles())
        except PolylogueMaterializationError:
            profiles = ()
    else:
        profiles = tuple(profiles)

    profile_by_id = {profile.conversation_id: profile for profile in profiles}

    children_by_parent: dict[str, list[ConversationLineage]] = defaultdict(list)
    for lineage in lineages:
        if lineage.parent_conversation_id:
            children_by_parent[lineage.parent_conversation_id].append(lineage)

    chains: list[ConversationForkChain] = []
    for parent_id, children in children_by_parent.items():
        forks = [c for c in children if c.branch_type == "fork"]
        sidechains = [c for c in children if c.branch_type == "sidechain"]
        subagents = [c for c in children if c.branch_type == "subagent"]
        meaningful_children = forks + sidechains  # exclude subagents and continuations

        parent_profile = profile_by_id.get(parent_id)
        parent_message_count = parent_profile.message_count if parent_profile else 0
        parent_substantial = parent_message_count >= _SUBSTANTIAL_MESSAGE_COUNT

        # A child fork is "substantial" if it crossed the threshold itself.
        substantial_fork = any(
            (profile_by_id.get(c.conversation_id, _NoProfile()).message_count or 0)
            >= _SUBSTANTIAL_MESSAGE_COUNT
            for c in meaningful_children
        )
        multi_explore = parent_substantial and len(meaningful_children) >= 2

        if not (substantial_fork or multi_explore):
            continue

        # Find the parent lineage row for metadata (provider, title, ts).
        parent_lineage = next(
            (lin for lin in lineages if lin.conversation_id == parent_id),
            None,
        )

        if substantial_fork and multi_explore:
            reason = "parent + multiple substantial forks"
        elif substantial_fork:
            reason = "fork crossed substantial-conversation threshold"
        else:
            reason = "parent forked into multiple substantial children"

        chains.append(ConversationForkChain(
            parent_id=parent_id,
            parent_provider=(parent_lineage.provider if parent_lineage else "unknown"),
            parent_title=(parent_lineage.title if parent_lineage else ""),
            parent_created_at=(parent_lineage.created_at if parent_lineage else None),
            parent_message_count=parent_message_count,
            fork_count=len(forks),
            sidechain_count=len(sidechains),
            subagent_count=len(subagents),
            children=tuple(_child_dict(c, profile_by_id) for c in meaningful_children),
            significance_reason=reason,
        ))

    chains.sort(key=lambda c: -(c.fork_count + c.sidechain_count))
    return chains


def render_fork_chains(chains: Sequence[ConversationForkChain], *, limit: int = 12) -> str:
    """Compact Markdown table of significant fork chains."""
    if not chains:
        return "_No substantial conversation forks detected in the window._"
    lines = [
        f"_{len(chains)} substantial fork chains detected_",
        "",
        "| Parent | Provider | Msgs | Forks | Sidechains | Subagents | Why surfaced |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for chain in chains[:limit]:
        title = (chain.parent_title or chain.parent_id[:16]).replace("|", "\\|")[:60]
        lines.append(
            f"| `{chain.parent_id[:12]}` {title} | {chain.parent_provider} | "
            f"{chain.parent_message_count} | {chain.fork_count} | "
            f"{chain.sidechain_count} | {chain.subagent_count} | "
            f"{chain.significance_reason} |"
        )
    return "\n".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────────


class _NoProfile:
    """Cheap stand-in when a child conversation has no profile row yet."""
    message_count = 0
    word_count = 0
    title = ""


def _child_dict(lineage: ConversationLineage, profile_by_id: dict) -> dict:
    profile = profile_by_id.get(lineage.conversation_id)
    return {
        "conversation_id": lineage.conversation_id,
        "branch_type": lineage.branch_type,
        "title": lineage.title,
        "message_count": profile.message_count if profile else 0,
        "word_count": profile.word_count if profile else 0,
        "created_at": lineage.created_at.isoformat() if lineage.created_at else None,
    }


__all__ = [
    "ConversationForkChain",
    "detect_fork_chains",
    "render_fork_chains",
]
