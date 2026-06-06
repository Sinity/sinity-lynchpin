"""Evidence graph edge builders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
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
    """Emit one cross-source edge per distinct (date, project, sourceA, sourceB) pair.

    Previous behavior chained all nodes in a (date, project) bucket, emitting N-1
    consecutive edges. ~97% of these were same-source pairs (git↔git, github↔github),
    adding no cross-evidence value while inflating edge count by ~90%.

    Fix: take one representative node per source per bucket, and emit edges only
    between distinct source pairs.
    """
    grouped: dict[tuple[date, str], dict[str, EvidenceNode]] = defaultdict(lambda: {})
    for node in nodes:
        if node.kind in {"analysis_artifact", "analysis_claim"}:
            continue
        if node.project:
            by_source = grouped[(node.date, node.project)]
            # Keep the first node per source (deterministic by id order)
            if node.source not in by_source:
                by_source[node.source] = node

    edges: list[EvidenceEdge] = []
    for (day, project), by_source in grouped.items():
        if len(by_source) < 2:
            continue
        sources = sorted(by_source.keys())
        for i, sourceA in enumerate(sources):
            for sourceB in sources[i + 1:]:
                nodeA = by_source[sourceA]
                nodeB = by_source[sourceB]
                edges.append(
                    EvidenceEdge(
                        nodeA.id, nodeB.id, "same_project_day",
                        f"{project} on {day} ({sourceA}↔{sourceB})", 0.4
                    )
                )
    return tuple(edges)


def temporal_overlap_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    """Emit cross-source overlap edges within each project.

    Treats point events (start == end, e.g. commits whose authored_at is the
    only timestamp) as instants that overlap an interval iff the instant
    falls inside it. Previously such nodes were excluded entirely by the
    ``end > start`` filter, making commits invisible to the overlap layer
    even though "commit landed during an AI session" is a high-value signal.

    Two intervals overlap iff max(starts) < min(ends). A point inside an
    interval is the degenerate case. Same-source pairs are skipped to avoid
    redundant edges (the within-source temporal grouping is captured
    elsewhere by temporal_proximity_edges).
    """
    grouped: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.project
            and node.start is not None
            and node.end is not None
            and node.end >= node.start
        ):
            grouped[node.project].append(node)
    edges: list[EvidenceEdge] = []
    for group in grouped.values():
        timed = sorted(group, key=node_time_sort_key)
        for idx, left in enumerate(timed):
            if left.start is None or left.end is None:
                continue
            left_start = as_local(left.start)
            left_end = as_local(left.end)
            for right in timed[idx + 1 :]:
                if right.start is None or right.end is None:
                    continue
                right_start = as_local(right.start)
                right_end = as_local(right.end)
                # Sorted by start, so right_start >= left_start. Past
                # left_end => no further right will overlap.
                if right_start > left_end:
                    break
                if left.source == right.source:
                    continue
                # Half-open interval semantics for interval-vs-interval
                # (consecutive sessions touching at boundary don't overlap).
                # For point events (commit with start==end), require the
                # point to fall inside the other's [start, end] range.
                left_is_point = left_start == left_end
                right_is_point = right_start == right_end
                if left_is_point and right_is_point:
                    overlap = left_start == right_start
                elif left_is_point:
                    overlap = right_start <= left_start <= right_end
                elif right_is_point:
                    overlap = left_start <= right_start <= left_end
                else:
                    overlap = max(left_start, right_start) < min(left_end, right_end)
                if overlap:
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

    from lynchpin.core.io import resolve_analysis_path

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
    """Compute file/symbol overlap edges for the in-memory graph.

    The name is retained for call-site compatibility. Earlier versions built a
    temporary DuckDB database and SQL views for every graph read; for the small
    per-window node set already in memory, a direct indexed join is much cheaper
    and keeps the same edge IDs/evidence shape.
    """
    del refresh_id

    work_events = [
        node
        for node in nodes
        if node.kind == "ai_work_event"
        and node.project is not None
        and node.start is not None
        and _filtered_paths((node.payload or {}).get("file_paths") or ())
    ]
    commits = [
        node
        for node in nodes
        if node.kind == "commit"
        and node.project is not None
        and node.start is not None
        and _filtered_paths((node.payload or {}).get("paths") or ())
    ]
    if not work_events or not commits:
        return ()

    edges: list[EvidenceEdge] = []
    commits_by_project: dict[str, list[EvidenceNode]] = defaultdict(list)
    for commit in commits:
        commits_by_project[commit.project or ""].append(commit)

    symbol_rows_by_sha = _symbol_rows_by_sha()
    max_gap = timedelta(hours=24)
    for work_event in work_events:
        we_at = as_local(work_event.start)
        we_paths = _filtered_paths((work_event.payload or {}).get("file_paths") or ())
        for commit in commits_by_project.get(work_event.project or "", ()):
            commit_at = as_local(commit.start)
            if abs(commit_at - we_at) > max_gap:
                continue
            commit_paths = _filtered_paths((commit.payload or {}).get("paths") or ())
            shared_paths = sorted(we_paths & commit_paths)
            if shared_paths:
                edges.append(
                    EvidenceEdge(
                        work_event.id,
                        commit.id,
                        "file_overlap",
                        _format_overlap_evidence("shared paths", shared_paths),
                        weight=0.85,
                    )
                )
            shared_symbols = _shared_symbols_for_commit(
                we_paths,
                symbol_rows_by_sha.get(
                    str(
                        (commit.payload or {}).get("sha")
                        or (commit.payload or {}).get("commit")
                        or ""
                    ),
                    (),
                ),
            )
            if shared_symbols:
                edges.append(
                    EvidenceEdge(
                        work_event.id,
                        commit.id,
                        "symbol_overlap",
                        _format_overlap_evidence("shared symbols", shared_symbols),
                        weight=0.95,
                    )
                )

    return tuple(edges)


def _filtered_paths(paths: Any) -> set[str]:
    return {path for path in (str(p) for p in paths if p) if not _high_fanout_path(path)}


def _high_fanout_path(path: str) -> bool:
    lowered = path.lower()
    return (
        "__init__.py" in lowered
        or "__pycache__" in lowered
        or path.endswith("Cargo.lock")
        or path.endswith("pyproject.toml")
        or path.endswith("package.json")
        or path.endswith("package-lock.json")
        or path.endswith("yarn.lock")
        or path.endswith("pnpm-lock.yaml")
        or path.endswith("flake.lock")
        or path.endswith("lock.json")
        or path.endswith(".gitignore")
        or path.startswith("node_modules/")
    )


def _format_overlap_evidence(prefix: str, items: Sequence[str]) -> str:
    preview = ", ".join(items[:3])
    suffix = f" (+{len(items) - 3})" if len(items) > 3 else ""
    return f"{prefix}: {preview}{suffix}"


def _symbol_rows_by_sha() -> dict[str, list[dict[str, Any]]]:
    rows_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entries in load_symbol_changes_index().values():
        for row in entries:
            sha = str(row.get("sha") or "")
            if sha:
                rows_by_sha[sha].append(row)
    return rows_by_sha


def _shared_symbols_for_commit(
    work_event_paths: set[str], symbol_rows: Sequence[dict[str, Any]]
) -> list[str]:
    symbols: set[str] = set()
    stripped_we_paths = {path.lstrip("/") for path in work_event_paths}
    for row in symbol_rows:
        symbol_path = str(row.get("path") or "").lstrip("/")
        if not symbol_path:
            continue
        if not any(
            ai_path.endswith(symbol_path) or symbol_path.endswith(ai_path)
            for ai_path in stripped_we_paths
        ):
            continue
        name = str(row.get("qualified_name") or "")
        if name:
            symbols.add(name)
    return sorted(symbols)


def mentions_project_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    """Emit mentions_project edges from raw-log nodes.

    Extracts project mentions from node summaries/payloads and emits edges to
    representative commits on the same day within the same project.

    Weight: 0.6 (between same_project_day=0.4 and references=0.9).
    """
    from ..core.project_mentions import projects_mentioned_in_text

    # Collect all commits by (date, project)
    commits_by_day_project: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind == "commit" and node.project:
            commits_by_day_project[(node.date, node.project)].append(node)

    edges: list[EvidenceEdge] = []
    for node in nodes:
        if node.kind != "raw_log":
            continue
        if node.project:
            # Skip nodes that already have a project attribution
            continue

        # Extract text to search for mentions
        text = node.summary or ""
        if node.payload:
            text += " " + str(node.payload.get("body") or "")

        mentioned = projects_mentioned_in_text(text)
        if not mentioned:
            continue

        # For each mentioned project, emit edge to a representative commit on same day
        for project in mentioned:
            candidates = commits_by_day_project.get((node.date, project), [])
            if not candidates:
                continue
            # Use most recent commit (sorted by id as tiebreaker)
            target = sorted(candidates, key=lambda n: (n.start or n.date, n.id))[-1]
            edges.append(
                EvidenceEdge(
                    node.id,
                    target.id,
                    "mentions_project",
                    f"{node.source} mentions {project}",
                    weight=0.6,
                )
            )

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
    "mentions_project_edges",
    "overlap_edges_via_substrate",
    "polylogue_work_event_tool_overlap_edges",
    "same_project_day_edges",
    "temporal_overlap_edges",
    "temporal_proximity_edges",
]
