"""Cross-source causal chain detection over evidence graph nodes.

Scans the evidence timeline for temporal sequences — not full causal
inference, but temporal proximity chains with type filtering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .evidence import EvidenceCaveat, degrade_confidence, propagate_caveats
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
    # M.16: caveats propagated from the constituent nodes. Reader can see
    # every layer's warning at chain-rendering time. Default empty so older
    # callers / tests don't have to construct a tuple explicitly.
    caveats: tuple[EvidenceCaveat, ...] = ()


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
    # Arc C.1: chains over the new ai_work_event substrate (Arc A) with
    # file-path overlap and per-event kind filtering. These are tighter than
    # the session-level `ai_assisted_implementation` chain — they require
    # both a Polylogue work-event (not just a session) AND that the
    # event's file_paths intersect the commit's paths.
    {
        "type": "ai_work_event_to_commit",
        "sequence": ("ai_work_event", "commit"),
        "max_gap_minutes": 240,
        "require_file_overlap": True,
        "label": "AI work event → file-overlapping commit",
    },
    {
        "type": "ai_research_to_impl_to_commit",
        "sequence": ("ai_work_event", "ai_work_event", "commit"),
        "max_gap_minutes": 480,
        "require_event_kinds": ("research", "implementation", None),
        "require_kind_tier_min": "medium",
        "label": "research session → impl session → commit",
    },
    {
        "type": "ai_debug_to_build_fix_to_fix",
        "sequence": ("ai_work_event", "terminal_pattern", "commit"),
        "max_gap_minutes": 90,
        "require_event_kinds": ("debugging", None, None),
        "require_pattern_kind_index": 1,  # terminal_pattern is index 1
        "require_pattern_kind": "build_fix_loop",
        "require_conventional_kinds": (None, None, "fix"),
        "label": "AI debug → build/fix loop → fix commit",
    },
)


# Arc K tier ordering used by `require_kind_tier_min` constraint below.
_TIER_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


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
    """Return a comparable, tz-aware datetime for the node, or None.

    Sources mix tz-aware (real archive data) and tz-naive (`datetime.combine(date, ...)`)
    timestamps. The chain detector sorts nodes by time, which raises TypeError
    when comparing naive vs aware. Anchor everything to UTC so the sort is
    well-defined; naive values are interpreted as UTC for the purposes of
    chain ordering only.
    """
    candidate: datetime | None = None
    if hasattr(node, "start") and isinstance(node.start, datetime):
        candidate = node.start
    elif hasattr(node, "timestamp") and isinstance(getattr(node, "timestamp", None), datetime):
        candidate = node.timestamp  # type: ignore[attr-defined]
    elif node.date:
        candidate = datetime.combine(node.date, datetime.min.time())
    if candidate is None:
        return None
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=timezone.utc)
    return candidate


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
        base_confidence = max(0.3, 0.85 - (avg_gap / max_gap) * 0.4)

        # M.16: propagate caveats from every node in the chain. A chain
        # crossing three uncertain layers carries the union of caveats
        # AND a degraded confidence so readers can't over-trust a "0.85
        # confidence" chain whose every node had a partial-readiness
        # warning attached.
        chain_caveats = propagate_caveats(*(node.caveats for node in window))
        confidence = round(degrade_confidence(base_confidence, chain_caveats), 2)

        chains.append(CausalChain(
            id=f"chain:{template['type']}:{window[0].date.isoformat()}:{i}",
            chain_type=template["type"],
            date=window[0].date,
            node_ids=tuple(n.id for n in window),
            node_kinds=kinds,
            summaries=tuple(n.summary for n in window),
            time_gaps_minutes=tuple(round(g, 1) for g in gaps),
            confidence=confidence,
            summary=template["label"],
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
    pattern_kind = template.get("require_pattern_kind")
    if pattern_kind:
        # Default index 0 (back-compat with templates that put the pattern
        # node first); allow `require_pattern_kind_index` for templates that
        # place terminal_pattern in a non-leading position.
        idx = int(template.get("require_pattern_kind_index", 0))
        if idx >= len(nodes):
            return False
        payload = getattr(nodes[idx], "payload", None) or {}
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

    # Arc C.1 — per-event kind filter. Each entry corresponds to a node in
    # the sequence; None is wildcard. Only meaningful when the matched node
    # is an ai_work_event whose payload carries a kind (Arc A).
    event_kinds = template.get("require_event_kinds")
    if event_kinds:
        for node, expected in zip(nodes, event_kinds):
            if expected is None:
                continue
            if node.kind != "ai_work_event":
                return False
            payload = getattr(node, "payload", None) or {}
            if payload.get("kind") != expected:
                return False

    # Arc C.1 — minimum kind tier (Arc K.3) over any ai_work_event nodes
    # in the window. Filters out chains where every event is "low" tier.
    tier_floor = template.get("require_kind_tier_min")
    if tier_floor:
        floor = _TIER_ORDER.get(str(tier_floor), 0)
        for node in nodes:
            if node.kind != "ai_work_event":
                continue
            payload = getattr(node, "payload", None) or {}
            tier = str(payload.get("kind_tier") or "")
            if _TIER_ORDER.get(tier, -1) < floor:
                return False

    # Arc C.1 — file-path overlap between consecutive ai_work_event ↔ commit
    # pairs. Walks the sequence and demands shared paths whenever an
    # ai_work_event is followed by (or follows) a commit. This is the
    # closure piece that makes the "AI session → code commit" chain
    # concrete: same file moved through both.
    if template.get("require_file_overlap"):
        for left, right in zip(nodes, nodes[1:]):
            if not _file_overlap_between(left, right):
                return False

    return True


def _file_overlap_between(left: EvidenceNode, right: EvidenceNode) -> bool:
    """True iff left's file_paths intersects right's commit paths (or vice versa).

    Returns True when neither side carries file_paths data, since a chain
    without that signal isn't proven to violate the constraint — caller
    should rely on other constraints to gate. Only short-circuits to False
    when both sides advertise paths and they don't overlap.
    """
    left_files = _node_file_paths(left)
    right_files = _node_file_paths(right)
    if not left_files and not right_files:
        return False
    if not left_files or not right_files:
        # Asymmetric: one side has paths, the other doesn't — can't prove
        # overlap. Treat as not-matched so the chain only fires on real
        # bilateral evidence.
        return False
    return bool(left_files & right_files)


def _node_file_paths(node: EvidenceNode) -> set[str]:
    """Pull file_paths from an ai_work_event payload or commit.payload.paths."""
    payload = getattr(node, "payload", None) or {}
    if node.kind == "ai_work_event":
        return {str(p) for p in payload.get("file_paths", []) if p}
    if node.kind == "commit":
        return {str(p) for p in payload.get("paths", []) if p}
    return set()


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
