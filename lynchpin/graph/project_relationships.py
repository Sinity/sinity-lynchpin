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
_SIGNAL_WEIGHT: dict[str, float] = {'shared_ai_work_events': 1.5, 'shared_ai_sessions': 1.0, 'shared_commits': 0.7, 'shared_raw_log': 0.4}

def build_project_relationships(graph: EvidenceGraph) -> ProjectRelationshipGraph:
    """Walk the graph; produce undirected weighted edges between projects."""
    pair_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    pair_evidence: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_session: dict[str, set[str]] = defaultdict(set)
    by_event: dict[str, set[str]] = defaultdict(set)
    by_raw_log: dict[str, set[str]] = defaultdict(set)
    session_node_ids: dict[str, list[str]] = defaultdict(list)
    event_node_ids: dict[str, list[str]] = defaultdict(list)
    raw_log_node_ids: dict[str, list[str]] = defaultdict(list)
    commit_refs: dict[str, dict[str, list[str]]] = defaultdict(lambda: {'prs': [], 'issues': []})

    def _accumulate(buckets: dict, signal: str, ids: dict[str, list[str]]) -> None:
        for key, projects in buckets.items():
            if len(projects) < 2:
                continue
            for a, b in combinations(sorted(projects), 2):
                pair_counts[a, b][signal] += 1
                slot = pair_evidence[a, b]
                if len(slot) < 6:
                    slot.extend(ids.get(key, [])[:2])
    _accumulate(by_session, 'shared_ai_sessions', session_node_ids)
    _accumulate(by_event, 'shared_ai_work_events', event_node_ids)
    _accumulate(by_raw_log, 'shared_raw_log', raw_log_node_ids)
    pr_to_projects: dict[str, set[str]] = defaultdict(set)
    issue_to_projects: dict[str, set[str]] = defaultdict(set)
    pr_evidence_nodes: dict[str, list[str]] = defaultdict(list)
    issue_evidence_nodes: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes:
        if node.kind != 'commit' or not node.project:
            continue
        refs = (node.payload or {}).get('github_refs') or {}
        if not isinstance(refs, dict):
            continue
        for pr in refs.get('prs', []) or ():
            key = f'pr#{pr}'
            pr_to_projects[key].add(node.project)
            pr_evidence_nodes[key].append(node.id)
        for issue in refs.get('issues', []) or ():
            key = f'issue#{issue}'
            issue_to_projects[key].add(node.project)
            issue_evidence_nodes[key].append(node.id)
    _accumulate(pr_to_projects, 'shared_commits', pr_evidence_nodes)
    _accumulate(issue_to_projects, 'shared_commits', issue_evidence_nodes)
    relationships: list[ProjectRelationship] = []
    for (a, b), counts in pair_counts.items():
        weight = sum((counts[signal] * _SIGNAL_WEIGHT.get(signal, 0.0) for signal in counts))
        relationships.append(ProjectRelationship(project_a=a, project_b=b, weight=round(weight, 2), signal_counts=dict(counts), sample_evidence_node_ids=tuple(pair_evidence[a, b][:6])))
    relationships.sort(key=lambda rel: -rel.weight)
    project_count = len({p for rel in relationships for p in (rel.project_a, rel.project_b)})
    return ProjectRelationshipGraph(relationships=tuple(relationships), project_count=project_count, edge_count=len(relationships))

def render_project_relationships(rel_graph: ProjectRelationshipGraph, *, limit: int=16) -> str:
    """Compact Markdown table of the strongest cross-project edges."""
    if not rel_graph.relationships:
        return "_No cross-project edges in this window's evidence graph._"
    lines = [f'_{rel_graph.edge_count} edges across {rel_graph.project_count} projects_', '', '| Project A | Project B | Weight | Signals |', '|---|---|---:|---|']
    for rel in rel_graph.relationships[:limit]:
        signals = ', '.join((f"{signal.removeprefix('shared_')}×{count}" for signal, count in sorted(rel.signal_counts.items(), key=lambda kv: -kv[1])))
        lines.append(f'| {rel.project_a} | {rel.project_b} | {rel.weight} | {signals} |')
    return '\n'.join(lines)
__all__ = ['ProjectRelationship', 'ProjectRelationshipGraph', 'build_project_relationships', 'render_project_relationships']
