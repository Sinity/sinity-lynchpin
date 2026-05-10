"""Multi-scale period synthesis over the evidence graph.

Bidirectional deterministic rollup:

- **Bottom-up pass**: build leaf periods (e.g. weeks within a month) by
  scoping evidence-graph nodes to each period, aggregating counts, anomalies,
  chains, and health metrics. Parents roll up from their children.
- **Top-down pass**: annotate each child with its ``role_in_parent``
  (``arc_opener``, ``crisis``, ``recovery``, ``steady``) using anomaly
  density and direction relative to siblings.

No LLM. The output is a typed tree consumable by narrative/dashboard layers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

from ..core.periods import (
    Period,
    child_keys,
    child_scale,
    parse_period,
    period_keys_in_range,
)
from .causal_chains import CausalChain, detect_chains
from .evidence_graph import EvidenceGraph, EvidenceNode, build_evidence_graph

ROLE_ARC_OPENER = "arc_opener"
ROLE_CRISIS = "crisis"
ROLE_RECOVERY = "recovery"
ROLE_STEADY = "steady"


@dataclass(frozen=True)
class HealthArc:
    sleep_score_avg: float | None
    sleep_score_min: float | None
    sleep_score_max: float | None
    sleep_score_trend: str  # "rising" | "falling" | "stable" | "unknown"
    sleep_hours_avg: float | None
    n_days: int


@dataclass(frozen=True)
class PeriodRollup:
    node_count: int
    node_kind_counts: dict[str, int]
    project_counts: dict[str, int]
    top_node_summaries: tuple[str, ...]


@dataclass(frozen=True)
class PeriodSynthesis:
    period: Period
    rollup: PeriodRollup
    salient_chains: tuple[CausalChain, ...]
    salient_anomalies: tuple[EvidenceNode, ...]
    health_arc: HealthArc | None
    caveats: tuple[str, ...]
    children: tuple["PeriodSynthesis", ...] = ()
    role_in_parent: str | None = None


def build_period_synthesis(
    *,
    scale: str,
    key: str,
    graph: EvidenceGraph | None = None,
    max_depth: int | None = None,
) -> Optional[PeriodSynthesis]:
    """Build a hierarchical synthesis for a top-level period.

    If ``graph`` is omitted, a graph is built for the period's date range.
    ``max_depth`` bounds recursion depth (None = full descent to ``day``).
    """
    period = parse_period(scale, key)
    if period is None:
        return None
    if graph is None:
        graph = build_evidence_graph(start=period.start, end=period.end)
    chains = detect_chains(graph.nodes, max_gap_minutes=60)
    return _build_recursive(period, graph, chains, max_depth=max_depth, depth=0)


def synthesize_window(
    *,
    start: date,
    end: date,
    scale: str = "month",
    graph: EvidenceGraph | None = None,
    max_depth: int | None = None,
) -> tuple[PeriodSynthesis, ...]:
    """Convenience wrapper: synthesize every period of ``scale`` within
    ``[start, end]``.
    """
    if graph is None:
        graph = build_evidence_graph(start=start, end=end)
    chains = detect_chains(graph.nodes, max_gap_minutes=60)
    keys = period_keys_in_range(scale, start, end)
    out: list[PeriodSynthesis] = []
    for k in keys:
        period = parse_period(scale, k)
        if period is None:
            continue
        out.append(_build_recursive(period, graph, chains, max_depth=max_depth, depth=0))
    return tuple(out)


def _build_recursive(
    period: Period,
    graph: EvidenceGraph,
    chains: tuple[CausalChain, ...],
    *,
    max_depth: int | None,
    depth: int,
) -> PeriodSynthesis:
    period_nodes = _slice_nodes(graph.nodes, period)
    period_chains = tuple(c for c in chains if period.start <= c.date <= period.end)

    sub_scale = child_scale(period.scale)
    children: tuple[PeriodSynthesis, ...] = ()
    if sub_scale and (max_depth is None or depth < max_depth) and sub_scale != "day":
        # Don't descend below the smallest meaningful scale by default; "day"
        # would be a separate synthesis surface.
        sub_keys = child_keys(period.scale, period.key)
        child_synths: list[PeriodSynthesis] = []
        for sk in sub_keys:
            sub_period = parse_period(sub_scale, sk)
            if sub_period is None:
                continue
            # Skip sub-periods entirely outside this period (e.g. ISO week
            # spilling across month boundary — keep only intersecting half).
            if sub_period.end < period.start or sub_period.start > period.end:
                continue
            child_synths.append(
                _build_recursive(sub_period, graph, chains, max_depth=max_depth, depth=depth + 1)
            )
        children = _annotate_roles(tuple(child_synths))

    rollup = _build_rollup(period_nodes, child_rollups=tuple(c.rollup for c in children))
    salient_chains = _select_salient_chains(period_chains, limit=5)
    salient_anomalies = _select_salient_anomalies(period_nodes, limit=5)
    health_arc = _build_health_arc(period_nodes)
    caveats = _collect_caveats(period_nodes, children)

    return PeriodSynthesis(
        period=period,
        rollup=rollup,
        salient_chains=salient_chains,
        salient_anomalies=salient_anomalies,
        health_arc=health_arc,
        caveats=caveats,
        children=children,
    )


def _slice_nodes(nodes: tuple[EvidenceNode, ...], period: Period) -> tuple[EvidenceNode, ...]:
    return tuple(n for n in nodes if period.start <= n.date <= period.end)


def _build_rollup(
    nodes: tuple[EvidenceNode, ...],
    *,
    child_rollups: tuple[PeriodRollup, ...] = (),
) -> PeriodRollup:
    kind_counts: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    for n in nodes:
        kind_counts[n.kind] += 1
        if n.project:
            project_counts[n.project] += 1
    # Top summaries: prefer evidence nodes that signal change — anomalies,
    # changepoints, and high-magnitude commits.
    salient = sorted(
        (n for n in nodes if n.kind in {"temporal_anomaly", "temporal_changepoint", "github_pr", "commit"}),
        key=lambda n: _node_salience(n),
        reverse=True,
    )
    top_summaries = tuple(n.summary for n in salient[:8])
    return PeriodRollup(
        node_count=len(nodes),
        node_kind_counts=dict(kind_counts),
        project_counts=dict(project_counts),
        top_node_summaries=top_summaries,
    )


def _node_salience(n: EvidenceNode) -> float:
    if n.kind == "temporal_anomaly":
        return 100.0 + float(n.payload.get("score", 0)) if n.payload else 100.0
    if n.kind == "temporal_changepoint":
        return 50.0 + abs(float(n.payload.get("magnitude", 0))) * 10 if n.payload else 50.0
    if n.kind == "github_pr":
        return 10.0
    if n.kind == "commit":
        return 1.0
    return 0.0


def _select_salient_anomalies(
    nodes: tuple[EvidenceNode, ...], *, limit: int
) -> tuple[EvidenceNode, ...]:
    anomalies = [n for n in nodes if n.kind == "temporal_anomaly"]
    anomalies.sort(
        key=lambda n: float(n.payload.get("score", 0)) if n.payload else 0,
        reverse=True,
    )
    return tuple(anomalies[:limit])


def _select_salient_chains(
    chains: tuple[CausalChain, ...], *, limit: int
) -> tuple[CausalChain, ...]:
    return tuple(sorted(chains, key=lambda c: c.confidence, reverse=True)[:limit])


def _build_health_arc(nodes: tuple[EvidenceNode, ...]) -> HealthArc | None:
    sleep_scores: list[float] = []
    sleep_hours: list[float] = []
    sleep_dates: list[date] = []
    for n in nodes:
        if n.kind != "sleep_quality" or not n.payload:
            continue
        score = n.payload.get("sleep_score") or n.payload.get("avg_score")
        if isinstance(score, (int, float)):
            sleep_scores.append(float(score))
            sleep_dates.append(n.date)
        hours = n.payload.get("sleep_hours")
        if isinstance(hours, (int, float)):
            sleep_hours.append(float(hours))
    if not sleep_scores and not sleep_hours:
        return None
    avg_score = sum(sleep_scores) / len(sleep_scores) if sleep_scores else None
    min_score = min(sleep_scores) if sleep_scores else None
    max_score = max(sleep_scores) if sleep_scores else None
    avg_hours = sum(sleep_hours) / len(sleep_hours) if sleep_hours else None

    trend = "unknown"
    if len(sleep_scores) >= 5:
        # Simple slope sign: compare first third vs last third.
        third = max(1, len(sleep_scores) // 3)
        first = sum(sleep_scores[:third]) / third
        last = sum(sleep_scores[-third:]) / third
        delta = last - first
        if abs(delta) < 2:
            trend = "stable"
        elif delta > 0:
            trend = "rising"
        else:
            trend = "falling"

    return HealthArc(
        sleep_score_avg=round(avg_score, 1) if avg_score is not None else None,
        sleep_score_min=min_score,
        sleep_score_max=max_score,
        sleep_score_trend=trend,
        sleep_hours_avg=round(avg_hours, 2) if avg_hours is not None else None,
        n_days=len(sleep_dates),
    )


def _collect_caveats(
    nodes: tuple[EvidenceNode, ...],
    children: tuple[PeriodSynthesis, ...],
) -> tuple[str, ...]:
    caveats: list[str] = []
    if not nodes:
        caveats.append("no evidence nodes within this period")
    if children and not all(c.rollup.node_count for c in children):
        caveats.append("at least one child period has no evidence")
    return tuple(caveats)


def _annotate_roles(children: tuple[PeriodSynthesis, ...]) -> tuple[PeriodSynthesis, ...]:
    """Top-down annotation: each child gets a ``role_in_parent`` based on
    sibling anomaly density and direction."""
    if not children:
        return children
    scores = [_anomaly_density(c) for c in children]
    if not any(scores):
        return tuple(replace(c, role_in_parent=ROLE_STEADY) for c in children)

    crisis_idx = scores.index(max(scores))
    annotated: list[PeriodSynthesis] = []
    for i, c in enumerate(children):
        if i == 0 and scores[0] > 0:
            role = ROLE_ARC_OPENER
        elif i == crisis_idx:
            role = ROLE_CRISIS
        elif i > 0 and scores[i] < scores[i - 1] * 0.5 and scores[i - 1] > 0:
            role = ROLE_RECOVERY
        else:
            role = ROLE_STEADY
        annotated.append(replace(c, role_in_parent=role))
    return tuple(annotated)


def _anomaly_density(synth: PeriodSynthesis) -> float:
    n_days = max((synth.period.end - synth.period.start).days + 1, 1)
    score = 0.0
    for n in synth.salient_anomalies:
        s = float(n.payload.get("score", 1.0)) if n.payload else 1.0
        score += s
    return score / n_days
