"""Chronological current-state timeline (M.10).

A first-class artifact alongside the context pack that walks the evidence
graph day-by-day and interleaves:

  - per-event rows (commits, AI work events, AI sessions, focus blocks,
    terminal sessions, terminal patterns, GitHub items, raw-log entries,
    analysis claims)
  - temporal signals (changepoints, anomalies, trends, rhythms — Arc N
    output already on the graph)
  - temporal evidence chains that fired on that day (ai_work_event → commit,
    build/fix loops, debug → fix sequences, etc.)
  - issue closure-chain transitions when the day's events involve them

Whereas the context pack's "Chronological Evidence" section is a 32-row
flat sample, this artifact is full-window, grouped by day, citation-rich
(includes node IDs, file paths, github_refs, kind tiers). It's intended as
a separate prompt input that an LLM can consult when the pack's summary
isn't enough.

Output is Markdown by default; JSON shape available for downstream
consumers. The artifact name (when persisted) is
``current_state_timeline.md`` — sibling of ``current_state_context_pack.md``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from .causal_chains import CausalChain, detect_chains
from .issue_closure_chain import IssueClosureChain, detect_closure_chains


@dataclass(frozen=True)
class TimelineRow:
    """One row in the timeline. Tries to carry enough citation data that
    a reader can verify the claim without going back to the graph."""
    when: datetime | None
    project: str | None
    kind: str
    source: str
    summary: str
    node_id: str
    payload_excerpt: dict[str, Any]


@dataclass(frozen=True)
class DaySection:
    """One day's worth of timeline rows + signals + chains that fired."""
    day: date
    rows: tuple[TimelineRow, ...]
    chains: tuple[CausalChain, ...]
    closure_transitions: tuple[IssueClosureChain, ...]
    signals: tuple[TimelineRow, ...]


@dataclass(frozen=True)
class CurrentStateTimeline:
    start: date
    end: date
    generated_at: datetime
    days: tuple[DaySection, ...]
    total_rows: int
    total_chains: int


def build_current_state_timeline(
    graph: EvidenceGraph,
    *,
    start: date,
    end: date,
) -> CurrentStateTimeline:
    """Walk the graph and produce a per-day chronological view."""
    rows_by_day: dict[date, list[TimelineRow]] = defaultdict(list)
    signals_by_day: dict[date, list[TimelineRow]] = defaultdict(list)

    for node in graph.nodes:
        if node.date < start or node.date > end:
            continue
        if node.kind in {"analysis_artifact"}:
            # Artifacts represent state-as-of, not events; suppress unless the
            # reader explicitly wants them. Analysis claims (the per-claim
            # nodes) DO go through the timeline because they're event-shaped.
            continue
        row = _node_to_row(node)
        if node.kind in _SIGNAL_KINDS:
            signals_by_day[node.date].append(row)
        else:
            rows_by_day[node.date].append(row)

    chains = detect_chains(list(graph.nodes), max_gap_minutes=120)
    chains_by_day: dict[date, list[CausalChain]] = defaultdict(list)
    for chain in chains:
        if start <= chain.date <= end:
            chains_by_day[chain.date].append(chain)

    closure_chains = detect_closure_chains(graph, reference=end)
    closure_by_day: dict[date, list[IssueClosureChain]] = defaultdict(list)
    for closure in closure_chains:
        # Attribute closure-chain to the day of last activity (closed_at,
        # or opened_at if still open).
        anchor = closure.closed_at or closure.opened_at
        if anchor is None:
            continue
        anchor_day = anchor.date()
        if anchor_day < start or anchor_day > end:
            continue
        # Only include broken/partial/orphaned in the timeline — the day
        # is interesting only when something is wrong.
        if closure.closure_status not in ("broken", "partial", "orphaned"):
            continue
        closure_by_day[anchor_day].append(closure)

    days: list[DaySection] = []
    all_days = sorted(set(rows_by_day) | set(signals_by_day) | set(chains_by_day) | set(closure_by_day))
    total_rows = 0
    total_chains = 0
    for day in all_days:
        rows = tuple(sorted(rows_by_day.get(day, []), key=_row_sort_key))
        signals = tuple(sorted(signals_by_day.get(day, []), key=_row_sort_key))
        day_chains = tuple(chains_by_day.get(day, []))
        day_closures = tuple(closure_by_day.get(day, []))
        if not rows and not signals and not day_chains and not day_closures:
            continue
        days.append(DaySection(
            day=day,
            rows=rows,
            chains=day_chains,
            closure_transitions=day_closures,
            signals=signals,
        ))
        total_rows += len(rows)
        total_chains += len(day_chains)

    return CurrentStateTimeline(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        days=tuple(days),
        total_rows=total_rows,
        total_chains=total_chains,
    )


def render_current_state_timeline(timeline: CurrentStateTimeline) -> str:
    """Render the timeline as Markdown, day-by-day, citation-rich."""
    lines: list[str] = [
        f"# Current-State Timeline ({timeline.start.isoformat()} → {timeline.end.isoformat()})",
        "",
        f"_Generated {timeline.generated_at.isoformat(timespec='seconds')} • "
        f"{timeline.total_rows} events • {timeline.total_chains} temporal evidence chains • "
        f"{len(timeline.days)} active days_",
        "",
        "_Sibling of the current-state context pack. The pack summarizes; this "
        "artifact gives full chronological detail with node IDs and citation "
        "data so claims can be traced back to evidence._",
        "",
    ]

    if not timeline.days:
        lines.append("_No evidence in the requested window._")
        return "\n".join(lines)

    for section in timeline.days:
        lines.append(f"## {section.day.isoformat()}")
        lines.append("")

        if section.signals:
            lines.append("**Temporal signals**")
            for row in section.signals:
                lines.append(f"- {_format_signal(row)}")
            lines.append("")

        if section.chains:
            lines.append("**Temporal evidence chains fired**")
            for chain in section.chains:
                node_refs = ", ".join(chain.node_ids)
                gaps = ", ".join(f"{g:.0f}m" for g in chain.time_gaps_minutes)
                lines.append(
                    f"- {chain.summary} (confidence {chain.confidence:.0%}, "
                    f"gaps {gaps}, nodes: {node_refs})"
                )
            lines.append("")

        if section.closure_transitions:
            lines.append("**Issue closure transitions (broken/partial/orphaned)**")
            for closure in section.closure_transitions:
                prs = ", ".join(closure.linked_pr_refs) if closure.linked_pr_refs else "—"
                shas = ", ".join(s[:8] for s in closure.closing_commit_shas) if closure.closing_commit_shas else "—"
                caveat = "; ".join(c.message for c in closure.caveats)
                lines.append(
                    f"- {closure.project} {closure.issue_ref} → **{closure.closure_status}** "
                    f"(prs={prs}, commits={shas}{f' — {caveat}' if caveat else ''})"
                )
            lines.append("")

        if section.rows:
            lines.append("| When | Project | Kind | Source | Evidence | Citation |")
            lines.append("|---|---|---|---|---|---|")
            for row in section.rows:
                when = row.when.strftime("%H:%M") if row.when else "—"
                lines.append(
                    f"| {when} | {row.project or 'unattributed'} | {row.kind} | "
                    f"{row.source} | {_summary_cell(row.summary)} | {_citation_cell(row)} |"
                )
            lines.append("")

    return "\n".join(lines).rstrip()


# ── Helpers ─────────────────────────────────────────────────────────────────

_SIGNAL_KINDS = frozenset({
    "temporal_changepoint",
    "temporal_trend",
    "temporal_anomaly",
    "temporal_rhythm",
    "readiness_forecast",
})


def _node_to_row(node: EvidenceNode) -> TimelineRow:
    payload = node.payload or {}
    excerpt: dict[str, Any] = {}
    # Pull the citation-relevant fields per node kind.
    if node.kind == "commit":
        excerpt = {
            "commit": payload.get("commit"),
            "github_refs": payload.get("github_refs"),
            "files_changed": payload.get("files_changed"),
            "paths_count": len(payload.get("paths") or ()),
        }
    elif node.kind == "ai_work_event":
        excerpt = {
            "event_id": payload.get("event_id"),
            "kind": payload.get("kind"),
            "kind_tier": payload.get("kind_tier"),
            "kind_source": payload.get("kind_source"),
            "duration_min": round((payload.get("duration_ms") or 0) / 60_000, 1),
            "files": (payload.get("file_paths") or [])[:3],
        }
    elif node.kind == "ai_session":
        excerpt = {
            "conversation_id": payload.get("conversation_id"),
            "provider": payload.get("provider"),
            "messages": payload.get("message_count"),
            "work_event_kind": payload.get("work_event_kind"),
        }
    elif node.kind in {"github_issue", "github_pr", "github_ref"}:
        excerpt = {
            "number": payload.get("number"),
            "state": payload.get("state"),
            "lifecycle": payload.get("lifecycle"),
        }
    elif node.kind == "terminal_session":
        excerpt = {
            "cwd": payload.get("cwd"),
            "command_count": payload.get("command_count"),
            "error_count": payload.get("error_count"),
            "top_commands": (payload.get("commands_summary") or [])[:3],
        }
    elif node.kind == "terminal_pattern":
        excerpt = {
            "kind": payload.get("kind"),
            "command_count": payload.get("command_count"),
            "error_count": payload.get("error_count"),
            "confidence": payload.get("confidence"),
        }
    elif node.kind == "raw_log":
        excerpt = {"source_path": payload.get("source_path"), "line_no": payload.get("line_no")}
    elif node.kind == "analysis_claim":
        excerpt = {
            "claim_type": payload.get("claim_type"),
            "artifact_name": payload.get("artifact_name"),
            "confidence": payload.get("confidence"),
        }
    elif node.kind in _SIGNAL_KINDS:
        # Pass through whatever the temporal signal carries.
        excerpt = {k: payload.get(k) for k in ("score", "kind", "metric", "magnitude", "z") if k in payload}
    return TimelineRow(
        when=node.start,
        project=node.project,
        kind=node.kind,
        source=node.source,
        summary=node.summary,
        node_id=node.id,
        payload_excerpt=excerpt,
    )


def _row_sort_key(row: TimelineRow) -> tuple:
    # tz-aware/-naive sort: anchor naive to UTC for ordering only.
    when = row.when
    if when is None:
        when_key = (1, "00:00")
    else:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when_key = (0, when.isoformat())
    return (when_key, row.kind, row.source, row.node_id)


def _format_signal(row: TimelineRow) -> str:
    excerpt = row.payload_excerpt
    bits = [f"{row.kind}: {row.summary}"]
    for key in ("score", "magnitude", "z"):
        if key in excerpt:
            bits.append(f"{key}={excerpt[key]}")
    return " — ".join(bits)


def _summary_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")[:100]


def _citation_cell(row: TimelineRow) -> str:
    parts: list[str] = [f"`{row.node_id}`"]
    excerpt = row.payload_excerpt
    if row.kind == "commit":
        sha = excerpt.get("commit")
        refs = excerpt.get("github_refs") or {}
        if sha:
            parts.append(f"sha={sha[:8]}")
        if isinstance(refs, dict) and (refs.get("prs") or refs.get("issues")):
            ref_str = ",".join(
                [f"pr#{n}" for n in refs.get("prs", [])]
                + [f"issue#{n}" for n in refs.get("issues", [])]
            )
            parts.append(ref_str)
    elif row.kind == "ai_work_event":
        kind = excerpt.get("kind") or "?"
        tier = excerpt.get("kind_tier") or ""
        tier_marker = f"[{tier}]" if tier else ""
        parts.append(f"{kind}{tier_marker}")
        files = excerpt.get("files") or []
        if files:
            parts.append("files=" + ",".join(f.rsplit("/", 1)[-1] for f in files[:2]))
    elif row.kind == "ai_session":
        parts.append(f"{excerpt.get('provider', '?')}/{excerpt.get('messages', 0)}msg")
    elif row.kind in {"github_issue", "github_pr"}:
        n = excerpt.get("number")
        state = excerpt.get("state")
        lc = excerpt.get("lifecycle")
        if n:
            parts.append(f"#{n} ({state or '?'}, {lc or '?'})")
    elif row.kind == "analysis_claim":
        parts.append(excerpt.get("claim_type") or "?")
    return _summary_cell(" • ".join(parts))


__all__ = [
    "CurrentStateTimeline",
    "DaySection",
    "TimelineRow",
    "build_current_state_timeline",
    "render_current_state_timeline",
]
