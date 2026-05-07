"""Cross-source causal chain detection over evidence graph nodes.

Scans the evidence timeline for temporal sequences — not full causal
inference, but temporal proximity chains with type filtering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .evidence_graph import EvidenceNode


@dataclass(frozen=True)
class CausalChain:
    id: str
    chain_type: str
    date: date
    node_ids: tuple[str, ...]
    node_kinds: tuple[str, ...]
    summaries: tuple[str, ...]
    time_gaps_minutes: tuple[float, ...]
    confidence: float
    summary: str
    payload: dict[str, Any]


_CHAIN_TEMPLATES = (
    {
        "type": "terminal_fix_test",
        "sequence": ("terminal_pattern", "commit", "commit"),
        "max_gap_minutes": 45,
        "require_pattern_kind": "build_fix_loop",
        "require_conventional_kinds": (None, "fix", "test"),
        "label": "build/fix loop → fix commit → test addition",
    },
    {
        "type": "ai_assisted_implementation",
        "sequence": ("ai_session", "commit"),
        "max_gap_minutes": 30,
        "label": "AI session → code commit",
    },
    {
        "type": "deep_work_delivery",
        "sequence": ("deep_work_block", "commit"),
        "max_gap_minutes": 120,
        "label": "deep work → code delivery",
    },
    {
        "type": "terminal_error_fix",
        "sequence": ("terminal_pattern", "commit"),
        "max_gap_minutes": 30,
        "require_pattern_kind": "retry_spiral",
        "require_conventional_kinds": (None, "fix"),
        "label": "terminal retry → fix commit",
    },
    {
        "type": "error_burst_resolution",
        "sequence": ("terminal_session", "terminal_session", "commit"),
        "max_gap_minutes": 60,
        "label": "high-error session → lower-error session → commit",
    },
)


def detect_chains(
    nodes: Sequence[EvidenceNode],
    *,
    max_gap_minutes: int = 60,
) -> tuple[CausalChain, ...]:
    """Detect causal chains from evidence graph nodes."""
    timed_nodes = sorted(
        (n for n in nodes if _node_time(n) is not None),
        key=lambda n: _node_time(n),
    )
    if len(timed_nodes) < 2:
        return ()

    chains: list[CausalChain] = []
    for template in _CHAIN_TEMPLATES:
        chains.extend(_match_template(timed_nodes, template, max_gap_minutes))

    return tuple(sorted(chains, key=lambda c: -c.confidence)[:30])


def _node_time(node: EvidenceNode) -> datetime | None:
    if hasattr(node, "start") and node.start:
        return node.start if isinstance(node.start, datetime) else None
    if hasattr(node, "timestamp") and node.timestamp:
        return node.timestamp if isinstance(node.timestamp, datetime) else None
    return datetime.combine(node.date, datetime.min.time()) if node.date else None


def _match_template(
    nodes: list[EvidenceNode],
    template: dict,
    global_max_gap: int,
) -> list[CausalChain]:
    seq = template["sequence"]
    seq_len = len(seq)
    max_gap = template.get("max_gap_minutes", global_max_gap)
    chains: list[CausalChain] = []

    for i in range(len(nodes) - seq_len + 1):
        window = nodes[i : i + seq_len]
        kinds = tuple(n.kind for n in window)

        if kinds != seq:
            continue

        if not _check_template_constraints(window, template):
            continue

        gaps = _time_gaps(window)
        if any(g is None or g > max_gap for g in gaps):
            continue

        avg_gap = sum(g for g in gaps if g is not None) / len(gaps) if gaps else 0
        confidence = max(0.3, 0.85 - (avg_gap / max_gap) * 0.4)

        chains.append(CausalChain(
            id=f"chain:{template['type']}:{window[0].date.isoformat()}:{i}",
            chain_type=template["type"],
            date=window[0].date,
            node_ids=tuple(n.id for n in window),
            node_kinds=kinds,
            summaries=tuple(n.summary for n in window),
            time_gaps_minutes=tuple(round(g, 1) for g in gaps),
            confidence=round(confidence, 2),
            summary=template["label"],
            payload={
                "template_type": template["type"],
                "node_count": seq_len,
                "max_gap_minutes": max_gap,
                "avg_gap_minutes": round(avg_gap, 1),
            },
        ))
    return chains


def _check_template_constraints(
    nodes: list[EvidenceNode], template: dict,
) -> bool:
    pattern_kind = template.get("require_pattern_kind")
    if pattern_kind:
        first = nodes[0]
        payload = getattr(first, "payload", None) or {}
        if payload.get("kind") != pattern_kind:
            return False

    conv_kinds = template.get("require_conventional_kinds")
    if conv_kinds:
        for node, expected in zip(nodes, conv_kinds):
            if expected is None:
                continue
            payload = getattr(node, "payload", None) or {}
            if payload.get("conventional_kind") != expected:
                return False

    return True


def _time_gaps(nodes: list[EvidenceNode]) -> list[float | None]:
    gaps: list[float | None] = []
    for j in range(len(nodes) - 1):
        t1 = _node_time(nodes[j])
        t2 = _node_time(nodes[j + 1])
        if t1 and t2:
            gaps.append((t2 - t1).total_seconds() / 60.0)
        else:
            gaps.append(None)
    return gaps


__all__ = ["CausalChain", "detect_chains"]
