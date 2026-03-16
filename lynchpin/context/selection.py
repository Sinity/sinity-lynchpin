"""Task-conditioned context selection with token budget.

Implements the "Budgeted Context, Not Maximal Context" principle:
score each packet for relevance to the query, greedily fill the budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ContextAssembly:
    """Result of select_context(): selected packets + metadata."""
    query: str
    budget_tokens: int
    tier: str
    packets: list[dict[str, Any]]
    total_estimated_tokens: int
    packet_types_included: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "budget_tokens": self.budget_tokens,
            "tier": self.tier,
            "total_estimated_tokens": self.total_estimated_tokens,
            "packet_types_included": self.packet_types_included,
            "packets": self.packets,
        }


def _score_packet(packet_type: str, packet_dict: dict[str, Any], query_terms: set[str]) -> float:
    """Score a packet for relevance to query terms.

    Returns a composite score: (topic_match * 0.5) + (recency * 0.3) + (density * 0.1) + type_priority
    """
    # Serialize to check for query term matches
    packet_str = json.dumps(packet_dict).lower()

    # topic_match: count matching query terms / max query terms
    matches = sum(1 for term in query_terms if term in packet_str)
    topic_match = min(matches / len(query_terms), 1.0) if query_terms else 0.0

    # recency_score based on packet type
    recency_score = {
        "days": 1.0,
        "weeks": 0.8,
        "months": 0.6,
        "quarters": 0.4,
        "years": 0.2,
        "episodes": 0.5,
        "themes": 0.5,
        "project_arcs": 0.5,
        "coverage": 0.5,
        "period": 0.7,
        "claims": 0.6,
    }.get(packet_type, 0.5)

    # evidence_density: normalize by 500 as max meaningful count
    evidence_count = 0
    if isinstance(packet_dict, dict):
        for key in ["chain_count", "signal_count", "command_count", "session_count"]:
            val = packet_dict.get(key)
            if isinstance(val, int):
                evidence_count += val
    evidence_density = min(evidence_count / 500.0, 1.0)

    # type_priority: bonus if query matches known project/topic names in the packet
    type_priority = 0.0
    for term in query_terms:
        if term in ["sinex", "sinnix", "polylogue", "knowledgebase", "scribe", "intercept"]:
            if term in packet_str:
                type_priority = 0.3
                break

    return (topic_match * 0.5) + (recency_score * 0.3) + (evidence_density * 0.1) + type_priority


def _estimate_tokens(packet_dict: dict[str, Any]) -> int:
    """Estimate token count from JSON serialization: ~4 chars per token."""
    return max(1, len(json.dumps(packet_dict)) // 4)


def select_context(
    query: str,
    *,
    budget_tokens: int = 4000,
    tier: Optional[str] = None,
    days: int = 90,
) -> ContextAssembly:
    """Select and rank context packets for a query within a token budget.

    Args:
        query: User query to match against packet content
        budget_tokens: Maximum tokens to consume (default 4000)
        tier: Budget tier ("compact", "standard", "full"). Auto-derived if not provided.
        days: Lookback window in days (default 90)

    Returns:
        ContextAssembly with selected packets, total tokens, and included types
    """
    # Lazy import to avoid circular dependency
    from .packet_builders import build_current_state

    # Derive tier from budget if not provided
    if tier is None:
        if budget_tokens <= 1500:
            tier = "compact"
        elif budget_tokens <= 6000:
            tier = "standard"
        else:
            tier = "full"

    # Build current state
    state = build_current_state(tier=tier, days=days)

    # Flatten all packets into (packet_type, packet_dict) pairs
    packets_with_scores: list[tuple[str, dict[str, Any], float, int]] = []

    # Single-dict sections
    for key in ["coverage", "period", "claims"]:
        if key in state and state[key]:
            packet = state[key]
            score = _score_packet(key, packet, set(query.lower().split()))
            tokens = _estimate_tokens(packet)
            packets_with_scores.append((key, packet, score, tokens))

    # List-of-dicts sections
    for key in ["days", "weeks", "months", "quarters", "years", "episodes", "themes", "project_arcs"]:
        if key in state and state[key]:
            for packet in state[key]:
                score = _score_packet(key, packet, set(query.lower().split()))
                tokens = _estimate_tokens(packet)
                packets_with_scores.append((key, packet, score, tokens))

    # Sort by score descending
    packets_with_scores.sort(key=lambda x: (-x[2], -x[3]))

    # Greedily pack until budget exhausted
    selected = []
    total_tokens = 0
    seen_types = set()

    for ptype, packet, score, tokens in packets_with_scores:
        if total_tokens + tokens <= budget_tokens:
            selected.append(packet)
            total_tokens += tokens
            seen_types.add(ptype)

    return ContextAssembly(
        query=query,
        budget_tokens=budget_tokens,
        tier=tier,
        packets=selected,
        total_estimated_tokens=total_tokens,
        packet_types_included=sorted(list(seen_types)),
    )
