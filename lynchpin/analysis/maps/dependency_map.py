"""Dependency map generator for sinex workspace crates."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from ..core.canonical import JsonObject
from ..core.io import resolve_analysis_path, save_json, save_text


def _run_cargo_metadata(repo_dir: str) -> JsonObject:
    if shutil.which("cargo") is None:
        return {
            "workspace_members": [],
            "packages": [],
            "resolve": {"nodes": []},
            "_lynchpin_status": "unavailable",
            "_lynchpin_reason": "cargo not found on PATH",
        }
    cmd = ['cargo', 'metadata', '--format-version', '1', '--locked']
    out = subprocess.check_output(cmd, cwd=repo_dir, text=True, stderr=subprocess.DEVNULL)
    data = json.loads(out)
    if not isinstance(data, dict):
        raise ValueError("cargo metadata returned non-object JSON")
    return cast(JsonObject, data)


def _workspace_packages(metadata: JsonObject, repo_dir: str) -> dict[str, JsonObject]:
    workspace_ids = set(metadata.get('workspace_members', []))
    packages_by_id = {pkg['id']: pkg for pkg in metadata.get('packages', [])}

    workspace: dict[str, JsonObject] = {}
    for pkg_id in workspace_ids:
        pkg = packages_by_id.get(pkg_id)
        if not pkg:
            continue
        manifest_path = pkg.get('manifest_path', '')
        rel_manifest = os.path.relpath(manifest_path, repo_dir).replace('\\', '/') if manifest_path else None
        crate_path = os.path.dirname(rel_manifest) if rel_manifest else None
        workspace[pkg_id] = {
            'id': pkg_id,
            'name': pkg.get('name'),
            'version': pkg.get('version'),
            'manifest_path': rel_manifest,
            'crate_path': crate_path,
        }
    return workspace


def _workspace_edges(metadata: JsonObject, workspace: Mapping[str, JsonObject]) -> list[tuple[str, str]]:
    resolve = metadata.get('resolve') or {}
    nodes = resolve.get('nodes') or []
    workspace_ids = set(workspace.keys())
    out: set[tuple[str, str]] = set()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        src_id = node.get('id')
        if src_id not in workspace_ids:
            continue

        for dep in node.get('deps', []):
            if not isinstance(dep, dict):
                continue
            dst_id = dep.get('pkg')
            if dst_id in workspace_ids and dst_id != src_id:
                out.add((src_id, dst_id))

    return sorted(out)


def _compute_degrees(node_ids: Sequence[str], edges: Sequence[tuple[str, str]]) -> dict[str, JsonObject]:
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)

    for src, dst in edges:
        out_deg[src] += 1
        in_deg[dst] += 1
        outgoing[src].add(dst)
        incoming[dst].add(src)

    rows: dict[str, JsonObject] = {}
    for node_id in node_ids:
        rows[node_id] = {
            'in_degree': in_deg[node_id],
            'out_degree': out_deg[node_id],
            'total_degree': in_deg[node_id] + out_deg[node_id],
            'dependencies': sorted(outgoing[node_id]),
            'dependents': sorted(incoming[node_id]),
        }
    return rows


def _transitive_reachability(node_ids: Sequence[str], edges: Sequence[tuple[str, str]]) -> dict[str, JsonObject]:
    graph: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        graph[src].append(dst)
        reverse[dst].append(src)

    def reach(start: str, g: Mapping[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in g.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    reach_rows: dict[str, JsonObject] = {}
    for node_id in node_ids:
        deps = reach(node_id, graph)
        deps.discard(node_id)
        dependents = reach(node_id, reverse)
        dependents.discard(node_id)
        reach_rows[node_id] = {
            'transitive_dependencies': len(deps),
            'transitive_dependents': len(dependents),
        }
    return reach_rows


def _row_int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    return value if isinstance(value, int) else 0


def _row_str(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key, ""))


def _render_markdown(payload: JsonObject) -> str:
    lines: list[str] = []
    lines.append('# Dependency Map\n')
    lines.append('## Scope\n')
    lines.append('- Source: `cargo metadata --format-version 1 --locked`\n')
    lines.append('- Graph contains workspace crate-to-crate dependencies only.\n')

    graph = cast(JsonObject, payload['graph'])
    lines.append('\n## Summary\n')
    lines.append(f"- Workspace crates: `{graph['node_count']}`\n")
    lines.append(f"- Internal dependency edges: `{graph['edge_count']}`\n")

    lines.append('\n## Top Central Crates\n')
    for row in cast(list[JsonObject], payload['top_central_crates'])[:12]:
        lines.append(
            f"- `{_row_str(row, 'crate')}`: degree `{_row_int(row, 'total_degree')}` "
            f"(in `{_row_int(row, 'in_degree')}`, out `{_row_int(row, 'out_degree')}`), "
            f"transitive_dependents `{_row_int(row, 'transitive_dependents')}`\n"
        )

    lines.append('\n## Strong Dependency Hubs\n')
    for row in cast(list[JsonObject], payload['top_dependency_hubs'])[:12]:
        lines.append(
            f"- `{_row_str(row, 'crate')}`: direct dependencies `{_row_int(row, 'out_degree')}`, "
            f"transitive_dependencies `{_row_int(row, 'transitive_dependencies')}`\n"
        )

    lines.append('\n## Strong Dependent Hubs\n')
    for row in cast(list[JsonObject], payload['top_dependent_hubs'])[:12]:
        lines.append(
            f"- `{_row_str(row, 'crate')}`: direct dependents `{_row_int(row, 'in_degree')}`, "
            f"transitive_dependents `{_row_int(row, 'transitive_dependents')}`\n"
        )

    return ''.join(lines)


def run_dependency_map(spec: JsonObject, out_file: str, markdown_out: str) -> JsonObject:
    repo_dir = spec['sinex']['repo']
    source_command = 'cargo metadata --format-version 1 --locked'
    metadata = _run_cargo_metadata(repo_dir)
    workspace = _workspace_packages(metadata, repo_dir)
    edges = _workspace_edges(metadata, workspace)

    node_ids = sorted(workspace.keys())
    deg = _compute_degrees(node_ids, edges)
    reach = _transitive_reachability(node_ids, edges)

    nodes: list[JsonObject] = []
    for node_id in node_ids:
        pkg = workspace[node_id]
        d = deg[node_id]
        r = reach[node_id]
        nodes.append(
            {
                'crate': pkg['name'],
                'crate_id': node_id,
                'version': pkg['version'],
                'crate_path': pkg['crate_path'],
                'manifest_path': pkg['manifest_path'],
                'in_degree': d['in_degree'],
                'out_degree': d['out_degree'],
                'total_degree': d['total_degree'],
                'dependencies': [workspace[x]['name'] for x in d['dependencies'] if x in workspace],
                'dependents': [workspace[x]['name'] for x in d['dependents'] if x in workspace],
                'transitive_dependencies': r['transitive_dependencies'],
                'transitive_dependents': r['transitive_dependents'],
            }
        )

    nodes.sort(
        key=lambda x: (
            x['total_degree'],
            x['transitive_dependents'],
            x['out_degree'],
            x['crate'],
        ),
        reverse=True,
    )

    edge_rows: list[JsonObject] = []
    for src, dst in edges:
        edge_rows.append(
            {
                'from': workspace[src]['name'],
                'to': workspace[dst]['name'],
                'from_id': src,
                'to_id': dst,
            }
        )
    edge_rows.sort(key=lambda x: (x['from'], x['to']))

    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'ecosystem': 'sinex',
        'source': {
            'repo': repo_dir,
            'command': source_command,
            'status': metadata.get('_lynchpin_status', 'ok'),
            'reason': metadata.get('_lynchpin_reason'),
        },
        'graph': {
            'node_count': len(nodes),
            'edge_count': len(edge_rows),
            'nodes': nodes,
            'edges': edge_rows,
        },
        'top_central_crates': nodes[:20],
        'top_dependency_hubs': sorted(nodes, key=lambda x: (x['out_degree'], x['transitive_dependencies']), reverse=True)[:20],
        'top_dependent_hubs': sorted(nodes, key=lambda x: (x['in_degree'], x['transitive_dependents']), reverse=True)[:20],
    }

    out_abs = resolve_analysis_path(out_file)
    md_abs = resolve_analysis_path(markdown_out)
    save_json(out_abs, payload, sort_keys=True)
    save_text(md_abs, _render_markdown(payload))

    return payload
