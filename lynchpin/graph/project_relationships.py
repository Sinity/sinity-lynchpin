"""Project-relationship graph (M.11).

Beyond per-project rows: edges *between* projects when they share evidence
that suggests operational coupling.

Relationship signals (each contributes to a weighted edge between A and B):

  - **shared_commits**: a commit message in project A references a commit
    SHA or PR/issue that belongs to project B (cross-repo references)
  - **shared_ai_sessions**: a polylogue session whose
    ``work_event_projects`` lists both A and B — an AI session that
    spanned both projects in the same conversation
  - **shared_ai_work_events**: an ai_work_event whose ``file_paths``
    span paths from both projects (tighter than session-level)
  - **shared_raw_log**: a raw-log entry that mentions both projects'
    canonical names

This is descriptive — high-frequency edges might mean genuine operational
coupling (sinex feeds lynchpin) or just shared agent attention (chrome
debugging happened to span sinex and lynchpin tabs).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations

from ..core.evidence_graph import EvidenceGraph


@dataclass(frozen=True)
class ProjectRelationship:
    project_a: str
    project_b: str
    weight: float
    signal_counts: dict[str, int]
    sample_evidence_node_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProjectRelationshipGraph:
    relationships: tuple[ProjectRelationship, ...]
    project_count: int
    edge_count: int


# Per-signal weight for the edge score. Sessions are higher than raw_log
# because session attribution is intentional; a session spanning two
# projects is durable cross-project work. Raw-log mentions are cheaper —
# co-occurrence in a single line can be incidental.
_SIGNAL_WEIGHT: dict[str, float] = {
    "shared_ai_work_events": 1.5,
    "shared_ai_sessions":    1.0,
    "shared_commits":        0.7,  # cross-repo PR/issue refs
    "shared_raw_log":        0.4,
}


def build_project_relationships(graph: EvidenceGraph) -> ProjectRelationshipGraph:
    """Walk the graph; produce undirected weighted edges between projects."""
    pair_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    pair_evidence: dict[tuple[str, str], list[str]] = defaultdict(list)

    # Multi-project nodes: a single node carries multiple projects via the
    # `_normalize_project` fanout in the source-add functions. Each node has
    # ONE project field after that fanout, but groups of nodes sharing a
    # conversation_id / event_id / source_path span multiple projects. Index
    # by these natural keys to find pairs.
    by_session: dict[str, set[str]] = defaultdict(set)
    by_event: dict[str, set[str]] = defaultdict(set)
    by_raw_log: dict[str, set[str]] = defaultdict(set)
    # Indexed by (natural_key, project) so the sample picker can keep only
    # the nodes belonging to the pair being accumulated. Previously samples
    # were keyed only by natural_key, which leaked node ids from off-pair
    # projects into every pair touching the shared session/event/raw_log.
    session_node_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    event_node_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    raw_log_node_ids: dict[tuple[str, str], list[str]] = defaultdict(list)

    commit_refs: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"prs": [], "issues": []}
    )

    for node in graph.nodes:
        project = node.project
        if not project:
            continue
        payload = node.payload or {}
        if node.kind == "ai_session":
            conv = payload.get("conversation_id") or node.id
            by_session[conv].add(project)
            session_node_ids[(conv, project)].append(node.id)
        elif node.kind == "ai_work_event":
            event_id = payload.get("event_id") or node.id
            by_event[event_id].add(project)
            event_node_ids[(event_id, project)].append(node.id)
        elif node.kind == "raw_log":
            line_key = f"{payload.get('source_path', '')}:{payload.get('line_no', '')}"
            if line_key.strip(":"):
                by_raw_log[line_key].add(project)
                raw_log_node_ids[(line_key, project)].append(node.id)
        elif node.kind == "commit":
            refs = payload.get("github_refs") or {}
            if isinstance(refs, dict):
                for pr in refs.get("prs", []) or ():
                    commit_refs[node.id]["prs"].append(f"{project}#pr#{pr}")
                for issue in refs.get("issues", []) or ():
                    commit_refs[node.id]["issues"].append(f"{project}#issue#{issue}")

    def _accumulate(
        buckets: dict[str, set[str]],
        signal: str,
        ids: dict[tuple[str, str], list[str]],
    ) -> None:
        for key, projects in buckets.items():
            if len(projects) < 2:
                continue
            for a, b in combinations(sorted(projects), 2):
                pair_counts[(a, b)][signal] += 1
                # Sample only nodes whose project is in the pair we're
                # crediting; previously every project's nodes from the shared
                # session leaked into every pair touching that session.
                slot = pair_evidence[(a, b)]
                if len(slot) < 6:
                    slot.extend(ids.get((key, a), [])[:1])
                    slot.extend(ids.get((key, b), [])[:1])

    _accumulate(by_session, "shared_ai_sessions", session_node_ids)
    _accumulate(by_event, "shared_ai_work_events", event_node_ids)
    _accumulate(by_raw_log, "shared_raw_log", raw_log_node_ids)

    # Cross-repo commit refs: previously this counted any case where the
    # same bare ``#N`` number appeared in two repos' commit subjects as a
    # coordination signal. Verified false-positive: sinex and polylogue
    # independently issue PRs in the same number range over the same
    # window, so e.g. polylogue#542 (flake deps) and sinex#542 (architecture
    # docs) collide on number despite being unrelated. That inflated
    # shared_commits for sinex↔polylogue to 545 (≈99% noise).
    #
    # True cross-repo coordination requires explicit ``owner/repo#N`` form
    # in the commit subject, which lynchpin.sources.github.extract_commit_refs
    # currently does NOT preserve (it strips owner/repo). Until the extractor
    # is upgraded, the only honest move is to not emit a coordination signal
    # from this data.

    relationships: list[ProjectRelationship] = []
    for (a, b), counts in pair_counts.items():
        weight = sum(
            counts[signal] * _SIGNAL_WEIGHT.get(signal, 0.0)
            for signal in counts
        )
        relationships.append(ProjectRelationship(
            project_a=a,
            project_b=b,
            weight=round(weight, 2),
            signal_counts=dict(counts),
            sample_evidence_node_ids=tuple(pair_evidence[(a, b)][:6]),
        ))

    relationships.sort(key=lambda rel: -rel.weight)
    project_count = len({p for rel in relationships for p in (rel.project_a, rel.project_b)})
    return ProjectRelationshipGraph(
        relationships=tuple(relationships),
        project_count=project_count,
        edge_count=len(relationships),
    )


def render_project_relationships(
    rel_graph: ProjectRelationshipGraph,
    *,
    limit: int = 16,
) -> str:
    """Compact Markdown table of the strongest cross-project edges."""
    if not rel_graph.relationships:
        return "_No cross-project edges in this window's evidence graph._"
    lines = [
        f"_{rel_graph.edge_count} edges across {rel_graph.project_count} projects_",
        "",
        "| Project A | Project B | Weight | Signals |",
        "|---|---|---:|---|",
    ]
    for rel in rel_graph.relationships[:limit]:
        signals = ", ".join(
            f"{signal.removeprefix('shared_')}×{count}"
            for signal, count in sorted(rel.signal_counts.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"| {rel.project_a} | {rel.project_b} | {rel.weight} | {signals} |")
    return "\n".join(lines)


__all__ = [
    "ProjectRelationship",
    "ProjectRelationshipGraph",
    "build_project_relationships",
    "render_project_relationships",
]
