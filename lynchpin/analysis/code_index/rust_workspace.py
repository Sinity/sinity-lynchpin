"""Rust workspace topology via cargo metadata.

Produces `active_rust_workspace_graph.json` — crate dependency graph with
centrality metrics and file-change correlation for each active Rust workspace.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json

_CARGO_TOML = "Cargo.toml"


def build_active_rust_graph(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot = load_json_object(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )
    changes_payload = load_json_object(
        file_changes_file or resolve_analysis_path("active_file_change_facts.json"),
        label="active file-change facts",
    )

    file_changes = _list(changes_payload, "file_changes")
    selected = set(projects or ())
    snapshot_projects = _project_map(snapshot, selected)

    workspace_rows: list[dict[str, Any]] = []
    for project_name, project_path in sorted(snapshot_projects.items()):
        root = Path(project_path)
        if not (root / _CARGO_TOML).exists():
            continue
        tool_run: dict[str, Any] = {"available": True}
        try:
            version = _cargo_version()
            tool_run["version"] = version
        except Exception:
            version = None
            tool_run["available"] = False

        if version is None:
            workspace_rows.append({
                "project": project_name,
                "status": "unavailable",
                "reason": "cargo not found on PATH",
                "tool_run": tool_run,
            })
            continue

        payload = _cargo_metadata(root)
        if payload is None:
            workspace_rows.append({
                "project": project_name,
                "status": "unavailable",
                "reason": tool_run.pop("reason", "cargo metadata failed"),
                "tool_run": tool_run,
            })
            continue

        tool_run["returncode"] = 0
        crates, edges = _build_crate_graph(payload)
        if not crates:
            workspace_rows.append({
                "project": project_name,
                "status": "empty",
                "reason": "no workspace crates found",
                "tool_run": tool_run,
            })
            continue

        _compute_degrees(crates, edges)
        _correlate_file_changes(crates, file_changes, project_name, str(root))
        _assign_risk(crates)

        workspace_rows.append({
            "project": project_name,
            "workspace_crate_count": len(crates),
            "internal_edge_count": len(edges),
            "crates": crates,
            "edges": edges,
            "tool_run": tool_run,
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "cargo metadata --format-version=1 --offline",
            "workspace_detection": "projects with Cargo.toml from active_project_snapshot",
            "degree": "in_degree counts workspace crates that depend on this crate; out_degree counts workspace crates this crate depends on",
            "risk_level": "derived from in_degree and file-change activity; high-centrality = in_degree >= 5, active-fringe = file_changes > 0 but not central, static-leaf = no file changes and low in_degree",
            "offline_only": "--offline flag used; network-disabled crates will fail with an unavailable marker",
        },
        "inputs": {
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
            "active_file_change_facts": str(file_changes_file or "active_file_change_facts.json"),
        },
        "projects": workspace_rows,
    }


def run_active_rust_graph(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_rust_graph(
        start=start, end=end, projects=projects,
        snapshot_file=snapshot_file, file_changes_file=file_changes_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _cargo_version() -> str:
    result = subprocess.run(
        ["cargo", "--version"],
        capture_output=True, text=True, timeout=15,
    )
    result.check_returncode()
    return result.stdout.strip()


def _cargo_metadata(project_root: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        ["cargo", "metadata", "--format-version=1", "--offline"],
        capture_output=True, text=True, timeout=60,
        cwd=str(project_root),
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _build_crate_graph(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    packages = payload.get("packages")
    if not isinstance(packages, list):
        return [], []

    workspace_ids = set(payload.get("workspace_members") or [])
    if not workspace_ids:
        return [], []

    workspace_root = _trailing_slash(str(payload.get("workspace_root") or ""))
    id_to_info: dict[str, dict[str, Any]] = {}
    name_to_ids: dict[str, list[str]] = defaultdict(list)

    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        pid = pkg.get("id")
        if pid not in workspace_ids:
            continue
        name = str(pkg.get("name") or "")
        manifest = str(pkg.get("manifest_path") or "")
        info = {
            "name": name,
            "crate_path": _relative_crate_path(manifest, workspace_root),
        }
        id_to_info[pid] = info
        name_to_ids[name].append(pid)

    workspace_names = {info["name"] for info in id_to_info.values()}
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()

    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        pid = pkg.get("id")
        if pid not in workspace_ids:
            continue
        from_name = id_to_info[pid]["name"]
        for dep in _list(pkg, "dependencies"):
            dep_name = str(dep.get("name") or "")
            if dep_name not in workspace_names or dep_name == from_name:
                continue
            edge_key = (from_name, dep_name)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({"from": from_name, "to": dep_name})

    crates = list(id_to_info.values())
    return crates, edges


def _compute_degrees(crates: list[dict[str, Any]], edges: list[dict[str, str]]) -> None:
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for edge in edges:
        out_deg[edge["from"]] += 1
        in_deg[edge["to"]] += 1
    for crate in crates:
        name = crate["name"]
        crate["in_degree"] = in_deg.get(name, 0)
        crate["out_degree"] = out_deg.get(name, 0)


def _correlate_file_changes(
    crates: list[dict[str, Any]],
    file_changes: list[dict[str, Any]],
    project: str,
    project_root: str,
) -> None:
    project_root_slash = _trailing_slash(project_root)
    counts: dict[str, int] = defaultdict(int)
    for change in file_changes:
        if not isinstance(change, dict):
            continue
        if str(change.get("project") or "") != project:
            continue
        path = str(change.get("path") or "")
        full_path = project_root_slash + path
        for crate in crates:
            crate_dir = _trailing_slash(str(crate["crate_path"]))
            if crate_dir and full_path.startswith(project_root_slash + crate_dir):
                counts[crate["name"]] += 1
    for crate in crates:
        crate["recent_file_changes"] = counts.get(crate["name"], 0)


def _assign_risk(crates: list[dict[str, Any]]) -> None:
    for crate in crates:
        in_deg = int(crate.get("in_degree") or 0)
        changes = int(crate.get("recent_file_changes") or 0)
        if in_deg >= 5:
            crate["risk_level"] = "high-centrality"
        elif changes > 0:
            crate["risk_level"] = "active-fringe"
        else:
            crate["risk_level"] = "static-leaf"


def _project_map(snapshot: dict[str, Any] | None, selected: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _list(snapshot, "projects"):
        if not isinstance(row, dict):
            continue
        name = str(row.get("project") or "")
        if selected and name not in selected:
            continue
        path = str(row.get("path") or "")
        if row.get("exists") is False:
            continue
        if path:
            result[name] = path
    return result


def _relative_crate_path(manifest_path: str, workspace_root_slash: str) -> str:
    if workspace_root_slash and manifest_path.startswith(workspace_root_slash):
        rel = manifest_path[len(workspace_root_slash):]
        parent = str(Path(rel).parent)
        return "" if parent == "." else parent
    return manifest_path


def _trailing_slash(s: str) -> str:
    return s if s.endswith("/") else s + "/"


def _list(payload: dict[str, Any] | None, key: str) -> list[Any]:
    if payload is None:
        return []
    result = payload.get(key)
    return result if isinstance(result, list) else []


__all__ = ["build_active_rust_graph", "run_active_rust_graph"]
