"""Small graph/stat helpers shared by analysis surfaces."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Iterable, Mapping


def normalize_graph(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
) -> tuple[list[str], list[tuple[str, str]], dict[str, set[str]], dict[str, set[str]]]:
    node_list = sorted(set(nodes))
    node_set = set(node_list)
    edge_list = sorted({(src, dst) for src, dst in edges if src in node_set and dst in node_set and src != dst})
    adjacency: dict[str, set[str]] = {node: set() for node in node_list}
    reverse: dict[str, set[str]] = {node: set() for node in node_list}
    for src, dst in edge_list:
        adjacency[src].add(dst)
        reverse[dst].add(src)
    return node_list, edge_list, adjacency, reverse


def compute_pagerank(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 40,
) -> dict[str, float]:
    node_list, _, adjacency, _ = normalize_graph(nodes, edges)
    if not node_list:
        return {}
    n = len(node_list)
    ranks = {node: 1.0 / n for node in node_list}
    base = (1.0 - damping) / n
    for _ in range(iterations):
        updated = {node: base for node in node_list}
        sinks = [node for node in node_list if not adjacency[node]]
        sink_rank = damping * sum(ranks[node] for node in sinks) / n
        for node in node_list:
            updated[node] += sink_rank
        for src in node_list:
            outgoing = adjacency[src]
            if not outgoing:
                continue
            share = damping * ranks[src] / len(outgoing)
            for dst in outgoing:
                updated[dst] += share
        ranks = updated
    return {node: round(score, 6) for node, score in ranks.items()}


def compute_betweenness(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
    *,
    normalized: bool = True,
) -> dict[str, float]:
    node_list, _, adjacency, _ = normalize_graph(nodes, edges)
    if not node_list:
        return {}
    betweenness = {node: 0.0 for node in node_list}
    for source in node_list:
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {node: [] for node in node_list}
        sigma = dict.fromkeys(node_list, 0.0)
        sigma[source] = 1.0
        distance = dict.fromkeys(node_list, -1)
        distance[source] = 0
        queue = deque([source])
        while queue:
            vertex = queue.popleft()
            stack.append(vertex)
            for neighbor in adjacency[vertex]:
                if distance[neighbor] < 0:
                    queue.append(neighbor)
                    distance[neighbor] = distance[vertex] + 1
                if distance[neighbor] == distance[vertex] + 1:
                    sigma[neighbor] += sigma[vertex]
                    predecessors[neighbor].append(vertex)

        dependency = dict.fromkeys(node_list, 0.0)
        while stack:
            vertex = stack.pop()
            if sigma[vertex] == 0:
                continue
            for predecessor in predecessors[vertex]:
                dependency_share = (sigma[predecessor] / sigma[vertex]) * (1.0 + dependency[vertex])
                dependency[predecessor] += dependency_share
            if vertex != source:
                betweenness[vertex] += dependency[vertex]

    if normalized and len(node_list) > 2:
        scale = 1.0 / ((len(node_list) - 1) * (len(node_list) - 2))
        betweenness = {node: round(score * scale, 6) for node, score in betweenness.items()}
    else:
        betweenness = {node: round(score, 6) for node, score in betweenness.items()}
    return betweenness


def _strongly_connected_components(
    nodes: list[str],
    adjacency: Mapping[str, set[str]],
) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in adjacency.get(node, set()):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in nodes:
        if node not in indices:
            visit(node)
    return components


def _condensation_depth(components: list[list[str]], adjacency: Mapping[str, set[str]]) -> int:
    if not components:
        return 0
    component_index = {
        node: idx
        for idx, component in enumerate(components)
        for node in component
    }
    dag: dict[int, set[int]] = defaultdict(set)
    indegree: dict[int, int] = defaultdict(int)
    for src, neighbors in adjacency.items():
        src_idx = component_index[src]
        for dst in neighbors:
            dst_idx = component_index[dst]
            if src_idx == dst_idx or dst_idx in dag[src_idx]:
                continue
            dag[src_idx].add(dst_idx)
            indegree[dst_idx] += 1

    queue = deque(idx for idx in range(len(components)) if indegree[idx] == 0)
    depth = {idx: 1 for idx in queue}
    best = 1 if queue else 0
    while queue:
        current = queue.popleft()
        best = max(best, depth[current])
        for nxt in dag.get(current, set()):
            depth[nxt] = max(depth.get(nxt, 1), depth[current] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return best


def compute_graph_metrics(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
) -> dict[str, float | int]:
    node_list, edge_list, adjacency, reverse = normalize_graph(nodes, edges)
    node_count = len(node_list)
    edge_count = len(edge_list)
    density = edge_count / (node_count * (node_count - 1)) if node_count > 1 else 0.0
    sccs = _strongly_connected_components(node_list, adjacency)
    nontrivial = [component for component in sccs if len(component) > 1]
    largest_scc = max((len(component) for component in sccs), default=0)
    return {
        "nodes": node_count,
        "edges": edge_count,
        "density": round(density, 6),
        "scc_count": len(sccs),
        "nontrivial_scc_count": len(nontrivial),
        "largest_scc_size": largest_scc,
        "condensation_depth": _condensation_depth(sccs, adjacency),
        "avg_out_degree": round(sum(len(adjacency[node]) for node in node_list) / max(node_count, 1), 4),
        "avg_in_degree": round(sum(len(reverse[node]) for node in node_list) / max(node_count, 1), 4),
    }


def distribution_stats(weights: Mapping[str, int | float]) -> dict[str, float | int]:
    filtered = {key: float(value) for key, value in weights.items() if value}
    if not filtered:
        return {
            "subsystems": 0,
            "top1_share": 0.0,
            "top5_share": 0.0,
            "entropy_bits": 0.0,
            "hhi": 0.0,
        }
    total = sum(filtered.values())
    ordered = sorted(filtered.values(), reverse=True)
    shares = [value / total for value in ordered]
    entropy = -sum(share * math.log2(share) for share in shares if share > 0)
    hhi = sum(share * share for share in shares)
    return {
        "subsystems": len(filtered),
        "top1_share": round(shares[0], 6),
        "top5_share": round(sum(shares[:5]), 6),
        "entropy_bits": round(entropy, 6),
        "hhi": round(hhi, 6),
    }
