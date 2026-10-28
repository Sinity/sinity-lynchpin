"""Cross-source temporal evidence chain detection over evidence graph nodes.

Scans the evidence timeline for temporal sequences. A relation names adjacent
observations among the kinds in its template, within the template's gap bound.
Other node kinds do not interrupt that sequence. These are temporal proximity
chains with type filtering, not causal inference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from ..core.evidence import EvidenceCaveat, degrade_confidence, propagate_caveats
from ..core.evidence_graph import EvidenceNode


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
    caveats: tuple[EvidenceCaveat, ...] = ()


_CHAIN_TEMPLATES = (
    {
        "type": "terminal_fix_test",
        "sequence": ("terminal_pattern", "commit", "commit"),
        "max_gap_minutes": 45,
        "require_pattern_kinds": ("build_fix_loop", None, None),
        "require_conventional_kinds": (None, "fix", "test"),
        "label": "build/fix loop → fix commit → test addition",
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
        "require_pattern_kinds": ("retry_spiral", None),
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
    max_gap_minutes: int | None = None,
) -> tuple[CausalChain, ...]:
    """Detect temporal evidence chains from evidence graph nodes."""
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
    """Return a comparable, tz-aware datetime for the node, or None.

    Date-only observations have unknown time and cannot support a gap claim.
    Naive timestamps are interpreted as UTC for ordering.
    """
    candidate: datetime | None = None
    if hasattr(node, "start") and isinstance(node.start, datetime):
        candidate = node.start
    elif hasattr(node, "timestamp") and isinstance(getattr(node, "timestamp", None), datetime):
        candidate = node.timestamp  # type: ignore[attr-defined]
    if candidate is None:
        return None
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=timezone.utc)
    return candidate


def _match_template(
    nodes: list[EvidenceNode],
    template: dict,
    global_max_gap: int | None,
) -> list[CausalChain]:
    seq = template["sequence"]
    seq_len = len(seq)
    template_max_gap = template["max_gap_minutes"]
    max_gap = min(template_max_gap, global_max_gap) if global_max_gap is not None else template_max_gap
    chains: list[CausalChain] = []

    # Match adjacent observations of the required kinds. Other kinds do not
    # change the relation between the observations named by a chain.
    relevant = [n for n in nodes if n.kind in seq]
    for i in range(len(relevant) - seq_len + 1):
        window = relevant[i : i + seq_len]
        kinds = tuple(n.kind for n in window)

        if kinds != seq:
            continue

        if not _check_template_constraints(window, template):
            continue

        gaps = _time_gaps(window)
        if any(g is None or g > max_gap for g in gaps):
            continue

        if template["type"] == "error_burst_resolution":
            first_errors = (window[0].payload or {}).get("error_count")
            second_errors = (window[1].payload or {}).get("error_count")
            if not isinstance(first_errors, int) or not isinstance(second_errors, int) or first_errors <= second_errors or first_errors <= 0:
                continue

        avg_gap = sum(g for g in gaps if g is not None) / len(gaps) if gaps else 0
        base_confidence = max(0.3, 0.85 - (avg_gap / max_gap) * 0.4)

        # M.16: propagate caveats from every node in the chain. A chain
        # crossing three uncertain layers carries the union of caveats
        # AND a degraded confidence so readers can't over-trust a "0.85
        # confidence" chain whose every node had a partial-readiness
        # warning attached.
        chain_caveats = propagate_caveats(*(node.caveats for node in window))
        confidence = round(degrade_confidence(base_confidence, chain_caveats), 2)

        support = "\0".join((template["type"], *(n.id for n in window)))
        chains.append(CausalChain(
            id=f"chain:{template['type']}:{sha256(support.encode()).hexdigest()[:20]}",
            chain_type=template["type"],
            date=window[0].date,
            node_ids=tuple(n.id for n in window),
            node_kinds=kinds,
            summaries=tuple(n.summary for n in window),
            time_gaps_minutes=tuple(round(g, 1) for g in gaps),
            confidence=confidence,
            summary=(
                f"terminal errors {first_errors} → {second_errors} → commit"
                if template["type"] == "error_burst_resolution"
                else template["label"]
            ),
            payload={
                "template_type": template["type"],
                "node_count": seq_len,
                "max_gap_minutes": max_gap,
                "avg_gap_minutes": round(avg_gap, 1),
                "base_confidence": round(base_confidence, 2),
                "caveat_count": len(chain_caveats),
            },
            caveats=chain_caveats,
        ))
    return chains


def _check_template_constraints(
    nodes: list[EvidenceNode], template: dict,
) -> bool:
    pattern_kinds = template.get("require_pattern_kinds")
    if pattern_kinds:
        for idx, expected in enumerate(pattern_kinds):
            if expected is None:
                continue
            if idx >= len(nodes):
                return False
            payload = getattr(nodes[idx], "payload", None) or {}
            if payload.get("kind") != expected:
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
