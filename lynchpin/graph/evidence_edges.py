"""Evidence graph edge builders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Sequence

from ..core.parse import as_local
from ..core.evidence_graph import EvidenceEdge, EvidenceNode

TOOL_COMMAND_TOKENS: dict[str, tuple[str, ...]] = {
    "Bash": ("pytest", "cargo", "just", "npm", "git", "make", "nix", "ruff", "mypy"),
    "Edit": (),
    "Read": (),
    "Write": (),
}


def same_project_day_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind in {"analysis_artifact", "analysis_claim"}:
            continue
        if node.project:
            grouped[(node.date, node.project)].append(node)
    edges: list[EvidenceEdge] = []
    for (day, project), group in grouped.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda node: (node.source, node.id))
        for left, right in zip(ordered, ordered[1:]):
            edges.append(
                EvidenceEdge(
                    left.id, right.id, "same_project_day", f"{project} on {day}", 0.4
                )
            )
    return tuple(edges)


def temporal_overlap_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    grouped: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.project
            and node.start is not None
            and node.end is not None
            and node.end > node.start
        ):
            grouped[node.project].append(node)
    edges: list[EvidenceEdge] = []
    for group in grouped.values():
        timed = sorted(group, key=node_time_sort_key)
        for idx, left in enumerate(timed):
            if left.start is None or left.end is None:
                continue
            left_end = as_local(left.end)
            for right in timed[idx + 1 :]:
                if right.start is None or right.end is None:
                    continue
                right_start = as_local(right.start)
                if right_start >= left_end:
                    break
                if left.source == right.source:
                    continue
                if as_local(right.end) > as_local(left.start):
                    edges.append(
                        EvidenceEdge(
                            left.id,
                            right.id,
                            "temporal_overlap",
                            f"{left.source} overlaps {right.source}",
                            0.7,
                        )
                    )
    return tuple(edges)


def load_symbol_changes_index() -> dict[str, list[dict[str, Any]]]:
    import json

    from ..analysis.core.io import resolve_analysis_path

    path = resolve_analysis_path("active_symbol_changes.json")
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"active symbol-change product is missing: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"active symbol-change product is not a JSON object: {path}")
    events = payload.get("events") or []
    if not isinstance(events, list):
        raise ValueError(f"active symbol-change product has non-list events: {path}")
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in events:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha")
        if sha:
            by_sha[str(sha)].append(entry)
    return dict(by_sha)


def extract_overlap_sources_from_nodes(
    nodes: Sequence[EvidenceNode],
) -> "tuple[list[Any], list[Any], list[dict[str, Any]]]":
    """Extract typed source rows from EvidenceNode payloads for substrate promotion."""
    from datetime import datetime as _dt

    from ..sources.git import GitCommitFact
    from ..sources.polylogue import WorkEvent

    work_events: list[WorkEvent] = []
    commit_facts: list[GitCommitFact] = []

    for node in nodes:
        payload = node.payload or {}
        if node.kind == "ai_work_event":
            event_id = payload.get("event_id")
            conversation_id = payload.get("conversation_id")
            if not event_id or not conversation_id:
                continue
            file_paths = tuple(p for p in (payload.get("file_paths") or []) if p)
            tools_used = tuple(t for t in (payload.get("tools_used") or []) if t)
            duration_ms = int(payload.get("duration_ms") or 0)
            kind = str(payload.get("kind") or "unknown")
            confidence = float(
                payload.get("kind_confidence") or payload.get("confidence") or 0.0
            )
            provider = str(payload.get("provider") or "")
            summary = node.summary or ""
            work_events.append(
                WorkEvent(
                    event_id=str(event_id),
                    conversation_id=str(conversation_id),
                    provider=provider,
                    kind=kind,
                    confidence=confidence,
                    start=node.start,
                    end=node.end,
                    duration_ms=duration_ms,
                    file_paths=file_paths,
                    tools_used=tools_used,
                    summary=summary,
                )
            )
        elif node.kind == "commit":
            sha = payload.get("commit") or payload.get("sha")
            if not sha or not node.project:
                continue
            paths = tuple(p for p in (payload.get("paths") or []) if p)
            authored_at = node.start or _dt.combine(node.date, _dt.min.time())
            commit_facts.append(
                GitCommitFact(
                    repo=node.project,
                    commit=str(sha),
                    authored_at=authored_at,
                    author=str(payload.get("author") or ""),
                    subject=str(payload.get("subject") or node.summary or ""),
                    lines_added=int(payload.get("lines_added") or 0),
                    lines_deleted=int(payload.get("lines_deleted") or 0),
                    lines_changed=int(payload.get("lines_changed") or 0),
                    files_changed=int(payload.get("files_changed") or len(paths)),
                    paths=paths,
                    path_roots=tuple(p for p in (payload.get("path_roots") or []) if p),
                )
            )

    symbol_changes = load_symbol_changes_index()
    symbol_rows: list[dict[str, Any]] = []
    for entries in symbol_changes.values():
        symbol_rows.extend(entries)

    return work_events, commit_facts, symbol_rows


def overlap_edges_via_substrate(
    nodes: Sequence[EvidenceNode],
    *,
    refresh_id: str,
) -> tuple[EvidenceEdge, ...]:
    """Promote overlap-source data and compute file/symbol overlap edges via SQL."""
    from lynchpin.substrate import apply_schema
    from lynchpin.substrate.work_ai import promote_ai_work_events
    from lynchpin.substrate.work_commits import promote_commits
    from lynchpin.substrate.work_symbols import promote_symbol_changes
    from lynchpin.substrate.graph import (
        compute_file_overlap_edges,
        compute_symbol_overlap_edges,
    )

    work_events, commit_facts, symbol_rows = extract_overlap_sources_from_nodes(nodes)
    if not work_events or not commit_facts:
        return ()

    project_by_event_id: dict[str, str | None] = {}
    for node in nodes:
        if node.kind == "ai_work_event":
            event_id = (node.payload or {}).get("event_id")
            if event_id:
                project_by_event_id[str(event_id)] = node.project

    def _project_resolver(ev: Any) -> str | None:
        return project_by_event_id.get(ev.event_id)

    edges: list[EvidenceEdge] = []
    seen_event_ids: set[str] = set()
    deduped_events = []
    for we in work_events:
        if we.event_id not in seen_event_ids:
            seen_event_ids.add(we.event_id)
            deduped_events.append(we)

    import duckdb

    mem = duckdb.connect(":memory:")
    apply_schema(mem)
    promote_commits(mem, refresh_id=refresh_id, facts=commit_facts)
    promote_ai_work_events(
        mem,
        refresh_id=refresh_id,
        events=deduped_events,
        project_resolver=_project_resolver,
    )
    if symbol_rows:
        promote_symbol_changes(mem, refresh_id=refresh_id, rows=symbol_rows)

    edges.extend(
        compute_file_overlap_edges(
            mem,
            we_refresh_id=refresh_id,
            commit_refresh_id=refresh_id,
        )
    )
    edges.extend(
        compute_symbol_overlap_edges(
            mem,
            we_refresh_id=refresh_id,
            commit_refresh_id=refresh_id,
        )
    )
    mem.close()

    return tuple(edges)


def polylogue_work_event_tool_overlap_edges(
    nodes: Sequence[EvidenceNode], *, max_gap_min: float = 60.0
) -> tuple[EvidenceEdge, ...]:
    """Bridge ai_work_event and terminal_session when tools and commands co-occur."""
    terminals_by_project: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.kind != "terminal_session"
            or node.project is None
            or node.start is None
        ):
            continue
        terminals_by_project[node.project].append(node)
    if not terminals_by_project:
        return ()

    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_min * 60
    for we_node in nodes:
        if (
            we_node.kind != "ai_work_event"
            or we_node.project is None
            or we_node.start is None
        ):
            continue
        tools_used = {
            str(t) for t in (we_node.payload or {}).get("tools_used", []) if t
        }
        candidate_tokens: set[str] = set()
        for tool in tools_used:
            candidate_tokens.update(TOOL_COMMAND_TOKENS.get(tool, ()))
        if not candidate_tokens:
            continue
        we_at = node_anchor_time(we_node)
        if we_at is None:
            continue
        for term_node in terminals_by_project.get(we_node.project, ()):
            term_at = node_anchor_time(term_node)
            if term_at is None:
                continue
            if abs((term_at - we_at).total_seconds()) > max_gap_s:
                continue
            commands = {
                str(c).split()[0]
                for c in (term_node.payload or {}).get("commands_summary", [])
                if c
            }
            shared = candidate_tokens & commands
            if not shared:
                continue
            preview = ", ".join(sorted(shared)[:3])
            edges.append(
                EvidenceEdge(
                    we_node.id,
                    term_node.id,
                    "tool_overlap",
                    f"co-occurring commands: {preview} (heuristic, not authorship)",
                    weight=0.5,
                )
            )
    return tuple(edges)


def temporal_proximity_edges(
    nodes: Sequence[EvidenceNode], *, max_gap_min: int = 90
) -> tuple[EvidenceEdge, ...]:
    grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.kind in {"analysis_artifact", "analysis_claim"}
            or node.project is None
            or node.start is None
        ):
            continue
        grouped[(node.date, node.project)].append(node)

    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_min * 60
    for group in grouped.values():
        timed = sorted(group, key=node_time_sort_key)
        for idx, left in enumerate(timed):
            left_at = node_anchor_time(left)
            if left_at is None:
                continue
            for right in timed[idx + 1 :]:
                right_at = node_anchor_time(right)
                if right_at is None:
                    continue
                gap_s = abs((right_at - left_at).total_seconds())
                if gap_s > max_gap_s:
                    break
                if left.source == right.source:
                    continue
                if (
                    left.start is not None
                    and right.start is not None
                    and left.end is not None
                    and right.end is not None
                    and as_local(left.end) > as_local(right.start)
                    and as_local(right.end) > as_local(left.start)
                ):
                    continue
                gap_min = round(gap_s / 60)
                edges.append(
                    EvidenceEdge(
                        left.id,
                        right.id,
                        "temporal_proximity",
                        f"{left.source} within {gap_min}m of {right.source}",
                        proximity_weight(gap_min),
                    )
                )
    return tuple(edges)


def node_time_sort_key(node: EvidenceNode) -> tuple[str, str, str]:
    anchor = node_anchor_time(node)
    return (anchor.isoformat() if anchor is not None else "", node.source, node.id)


def node_anchor_time(node: EvidenceNode) -> datetime | None:
    if node.start is None:
        return None
    return as_local(node.start)


def proximity_weight(gap_min: int) -> float:
    if gap_min <= 15:
        return 0.82
    if gap_min <= 60:
        return 0.7
    return 0.58


__all__ = [
    "load_symbol_changes_index",
    "overlap_edges_via_substrate",
    "polylogue_work_event_tool_overlap_edges",
    "same_project_day_edges",
    "temporal_overlap_edges",
    "temporal_proximity_edges",
]
