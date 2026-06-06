"""Substrate confidence matrix (M.17).

Per evidence layer × per quality dimension, render a heat map at the top of
the context pack so the reader sees substrate quality before reading any
claims. Distinguishes "I have lots of data" from "the data I have is
trustworthy enough to act on."

Dimensions:

  - **coverage**     — source readiness status (available / partial /
                       missing) mapped to high/medium/low tiers
  - **date_coverage** — whether observed source date bounds cover, overlap,
                       or miss the queried analysis window
  - **kind_quality** — Arc K agreement rate over ai_work_event nodes;
                       only meaningful for the polylogue layer, "—" elsewhere
  - **cross_source** — fraction of project/days where this source has
                       evidence AND at least one other source also has
                       evidence on the same project/day; surfaces isolated-
                       source patterns ("git lit up but nothing else
                       co-occurred — the AI sessions claim is graph-orphan")

Output is part of the pack header so a reader skimming the top of the
artifact sees substrate quality immediately.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

from ..core.evidence import SourceReadinessReport
from ..core.evidence_graph import EvidenceGraph
from .work_correlation import CorrelatedWorkDay


Tier = Literal["high", "medium", "low", "absent", "n_a"]


@dataclass(frozen=True)
class ConfidenceCell:
    tier: Tier
    detail: str  # human-facing one-liner


@dataclass(frozen=True)
class LayerRow:
    layer: str
    coverage: ConfidenceCell
    date_coverage: ConfidenceCell
    kind_quality: ConfidenceCell
    cross_source: ConfidenceCell


@dataclass(frozen=True)
class SubstrateConfidenceMatrix:
    rows: tuple[LayerRow, ...]
    overall_tier: Tier
    overall_summary: str


# ── public API ───────────────────────────────────────────────────────────────


def build_substrate_confidence_matrix(
    *,
    readiness: SourceReadinessReport,
    graph: EvidenceGraph,
    correlation_rows: Sequence[CorrelatedWorkDay] = (),
) -> SubstrateConfidenceMatrix:
    """Build the matrix from already-loaded readiness + graph + correlation rows.

    Caller is responsible for providing all three; this function does no
    extra source loads. Cheap to compute relative to the rest of the pack.
    """
    layer_names = _ordered_layers(readiness, graph)
    layer_readiness = {item.source: item for item in readiness.sources}

    cross_source_index = _cross_source_index(correlation_rows)
    kind_quality_polylogue = _kind_quality_for_polylogue(graph)

    rows: list[LayerRow] = []
    tier_counts: Counter[str] = Counter()
    for layer in layer_names:
        ready = layer_readiness.get(layer)
        coverage = _coverage_cell(ready)
        date_coverage = _date_coverage_cell(ready, window_start=readiness.start, window_end=readiness.end)
        kind_quality = (
            kind_quality_polylogue if layer == "polylogue"
            else ConfidenceCell(tier="n_a", detail="—")
        )
        cross_source = _cross_source_cell(layer, cross_source_index, window_days=_window_days(readiness))
        row = LayerRow(
            layer=layer,
            coverage=coverage,
            date_coverage=date_coverage,
            kind_quality=kind_quality,
            cross_source=cross_source,
        )
        rows.append(row)
        for cell in (coverage, date_coverage, kind_quality, cross_source):
            if cell.tier != "n_a":
                tier_counts[cell.tier] += 1

    overall_tier, overall_summary = _overall_tier(tier_counts)
    return SubstrateConfidenceMatrix(
        rows=tuple(rows),
        overall_tier=overall_tier,
        overall_summary=overall_summary,
    )


def render_substrate_confidence_matrix(matrix: SubstrateConfidenceMatrix) -> str:
    """Render as a Markdown heat-map table for the pack header."""
    lines = [
        f"_Overall substrate confidence: **{matrix.overall_tier}** — {matrix.overall_summary}_",
        "",
        "| Layer | Coverage | Date Coverage | Kind Quality | Cross-source |",
        "|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        lines.append(
            f"| {row.layer} | {_render_cell(row.coverage)} | "
            f"{_render_cell(row.date_coverage)} | {_render_cell(row.kind_quality)} | "
            f"{_render_cell(row.cross_source)} |"
        )
    return "\n".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────────


_TIER_GLYPH: dict[Tier, str] = {
    "high":   "✅",
    "medium": "⚠️",
    "low":    "❌",
    "absent": "⊘",
    "n_a":    "—",
}


def _render_cell(cell: ConfidenceCell) -> str:
    if cell.tier == "n_a":
        return cell.detail
    return f"{_TIER_GLYPH[cell.tier]} {cell.detail}"


def _ordered_layers(readiness: SourceReadinessReport, graph: EvidenceGraph) -> list[str]:
    """Stable layer order that puts highest-leverage sources first."""
    canonical = [
        "git",
        "polylogue",
        "activitywatch",
        "terminal",
        "github",
        "raw_log",
        "browser",
        "sleep",
        "health",
        "analysis",
    ]
    seen = {item.source for item in readiness.sources}
    # Add `github` synthetically when older readiness inputs omit the
    # github_context product but the graph already has GitHub lifecycle nodes.
    has_github = any(node.kind in {"github_issue", "github_pr", "github_ref"} for node in graph.nodes)
    if has_github and "github" not in seen:
        seen.add("github")
    ordered = [layer for layer in canonical if layer in seen]
    # Append any seen layers not in canonical (so future sources surface).
    for layer in seen:
        if layer not in ordered:
            ordered.append(layer)
    return ordered


def _coverage_cell(ready) -> ConfidenceCell:
    if ready is None:
        return ConfidenceCell(tier="absent", detail="not tracked")
    status = ready.status
    if status == "available":
        return ConfidenceCell(tier="high", detail="available")
    if status == "partial":
        return ConfidenceCell(tier="medium", detail=f"partial: {_short(ready.reason)}")
    if status == "missing":
        return ConfidenceCell(tier="low", detail=f"missing: {_short(ready.reason)}")
    if status == "out_of_range":
        return ConfidenceCell(tier="low", detail=f"out_of_range: {_short(ready.reason)}")
    if status == "blocked":
        return ConfidenceCell(tier="low", detail=f"blocked: {_short(ready.reason)}")
    return ConfidenceCell(tier="absent", detail=str(status))


def _date_coverage_cell(ready, *, window_start: date, window_end: date) -> ConfidenceCell:
    if ready is None or ready.last_date is None:
        return ConfidenceCell(tier="absent", detail="no date bounds")
    first = ready.first_date
    last = ready.last_date
    if first is not None and first <= window_start and last >= window_end:
        return ConfidenceCell(tier="high", detail="covers window")
    if last < window_start:
        return ConfidenceCell(tier="low", detail=f"ends before window ({last.isoformat()})")
    if first is not None and first > window_end:
        return ConfidenceCell(tier="low", detail=f"starts after window ({first.isoformat()})")
    if first is not None:
        return ConfidenceCell(tier="medium", detail=f"partial overlap {first.isoformat()} → {last.isoformat()}")
    if last >= window_end:
        return ConfidenceCell(tier="medium", detail=f"last date reaches window end ({last.isoformat()})")
    return ConfidenceCell(tier="medium", detail=f"last date inside window ({last.isoformat()})")


def _kind_quality_for_polylogue(graph: EvidenceGraph) -> ConfidenceCell:
    """Per Arc K: agreement rate across ai_work_event nodes."""
    work_events = [n for n in graph.nodes if n.kind == "ai_work_event"]
    if not work_events:
        return ConfidenceCell(tier="absent", detail="no work events")
    high = sum(
        1 for n in work_events
        if (n.payload or {}).get("kind_tier") == "high"
    )
    medium = sum(
        1 for n in work_events
        if (n.payload or {}).get("kind_tier") == "medium"
    )
    total = len(work_events)
    high_pct = high / total
    if high_pct >= 0.75:
        return ConfidenceCell(tier="high", detail=f"{high_pct:.0%} high tier ({high}/{total})")
    if (high + medium) / total >= 0.75:
        return ConfidenceCell(
            tier="medium",
            detail=f"{high}/{total} high + {medium}/{total} medium",
        )
    return ConfidenceCell(tier="low", detail=f"only {high}/{total} high tier")


def _cross_source_index(rows: Sequence[CorrelatedWorkDay]) -> dict[str, set[date]]:
    """Per source layer, the set of project/day rows where the source
    co-occurred with at least one other source. Indexed by layer → date set
    so the cross_source cell can be a fraction of all observed days."""
    index: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        if len(row.sources) < 2:
            continue
        for source in row.sources:
            index[source].add(row.date)
    # Single-source dates: track separately so we can show "alone X / total Y".
    return dict(index)


def _cross_source_cell(
    layer: str,
    cross_source_index: dict[str, set[date]],
    *,
    window_days: int,
) -> ConfidenceCell:
    layer_dates = cross_source_index.get(layer, set())
    co_occurring = len(layer_dates)
    if window_days <= 0:
        return ConfidenceCell(tier="n_a", detail="—")
    if co_occurring == 0:
        return ConfidenceCell(tier="low", detail=f"0/{window_days} days co-occurring")
    ratio = co_occurring / window_days
    if ratio >= 0.5:
        return ConfidenceCell(tier="high", detail=f"{co_occurring}/{window_days} days")
    if ratio >= 0.2:
        return ConfidenceCell(tier="medium", detail=f"{co_occurring}/{window_days} days")
    return ConfidenceCell(tier="low", detail=f"{co_occurring}/{window_days} days")


def _window_days(readiness: SourceReadinessReport) -> int:
    return (readiness.end - readiness.start).days + 1


def _overall_tier(tier_counts: Counter[str]) -> tuple[Tier, str]:
    total = sum(tier_counts.values())
    if total == 0:
        return "absent", "no measurable layers"
    high = tier_counts.get("high", 0)
    medium = tier_counts.get("medium", 0)
    low = tier_counts.get("low", 0) + tier_counts.get("absent", 0)
    summary = f"{high} high / {medium} medium / {low} low"
    if high / total >= 0.6:
        return "high", summary
    if (high + medium) / total >= 0.6:
        return "medium", summary
    return "low", summary


def _short(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= 60 else text[:57] + "..."


__all__ = [
    "ConfidenceCell",
    "LayerRow",
    "SubstrateConfidenceMatrix",
    "Tier",
    "build_substrate_confidence_matrix",
    "render_substrate_confidence_matrix",
]
