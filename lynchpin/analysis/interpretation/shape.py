"""Active code hotspots and quality guardrail materializers.

Produce active_code_hotspots.json and active_quality_guardrails.json from
existing file-change facts and project snapshots — no new third-party tooling.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


_GUARDRAIL_CATEGORIES = {"test", "ci", "lint", "type", "benchmark", "config", "nix", "guardrail"}
_GUARDRAIL_PATH_PATTERNS = (
    "test", "tests", "spec", "conftest",
    ".github/workflows", ".github/actions",
    "pyproject.toml", "mypy.ini", "setup.cfg", ".mypy",
    ".ruff.toml", "ruff.toml",
    "tox.ini", "Makefile", "Justfile", "justfile",
    "Cargo.toml", "Cargo.lock",
    "flake.lock", "flake.nix", "default.nix", "shell.nix",
    ".envrc", "direnv",
    "ci", "docker", "Dockerfile",
    "benches", "benchmarks",
)
_CENTRAL_PATH_SIGNALS = (
    "core", "src", "lib", "main", "cli", "api",
    "gateway", "ingest", "server", "daemon",
    "schema", "db", "storage", "repository",
    "sdk", "client", "protocol",
)


def build_active_hotspots(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build file-level and module-level hotspot facts from active file changes."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    changes_payload = _load_payload(
        file_changes_file or resolve_analysis_path("active_file_change_facts.json"),
        label="active file-change facts",
    )
    snapshot_payload = _load_payload(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )

    file_changes = _list(changes_payload, "file_changes")
    selected = set(projects or ())

    per_project: dict[str, Counter[str]] = defaultdict(Counter)
    per_path_root: dict[str, Counter[str]] = defaultdict(Counter)
    per_category: dict[str, Counter[str]] = defaultdict(Counter)
    path_dates: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for row in file_changes:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        path_root = str(row.get("path_root") or "")
        category = str(row.get("category") or "other")
        day = str(row.get("date") or "")

        per_project[project][path] += 1
        per_path_root[project][path_root] += 1
        per_category[project][category] += 1
        if day:
            path_dates[project][path].add(day)

    project_rows: list[dict[str, Any]] = []
    for project_name in sorted(set(per_project) | set(per_path_root)):
        file_counts = per_project.get(project_name, Counter())
        root_counts = per_path_root.get(project_name, Counter())
        cat_counts = per_category.get(project_name, Counter())
        dates_by_path = path_dates.get(project_name, {})

        top_files = _ranked(file_counts, 20)
        top_roots = _ranked(root_counts, 15)
        top_cats = _ranked(cat_counts, 10)
        gate_info = _gates_for(snapshot_payload, project_name)

        hotspots = []
        for path, count in top_files:
            active_days = len(dates_by_path.get(path, set()))
            is_central = _is_central(path)
            is_guardrail = _is_guardrail(path, _category_for(path, top_cats))
            hotspots.append({
                "path": path,
                "change_count": count,
                "active_days": active_days,
                "central": is_central,
                "guardrail": is_guardrail,
                "signals": _path_signals(path),
            })

        guardrail_moved = bool(cat_counts.get("test") or cat_counts.get("ci") or cat_counts.get("lint"))
        central_moved = sum(1 for h in hotspots if h["central"])
        guardrail_changes = sum(1 for h in hotspots if h["guardrail"])

        project_rows.append({
            "project": project_name,
            "changed_file_count": len(file_counts),
            "changed_path_root_count": len(root_counts),
            "hotspot_files": hotspots,
            "top_path_roots": [{"path_root": pr, "change_count": c} for pr, c in top_roots],
            "top_categories": [{"category": cat, "change_count": c} for cat, c in top_cats],
            "central_files_changed": central_moved,
            "guardrail_files_changed": guardrail_changes,
            "quality_gates_detected": gate_info.get("gates", []),
            "quality_gate_count": gate_info.get("count", 0),
            "interpretation": {
                "guardrail_moved": guardrail_moved,
                "central_surface_touched": central_moved > 0,
                "primary_category": top_cats[0][0] if top_cats else "none",
            },
            "caveats": _hotspot_caveats(cat_counts, file_counts),
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "aggregated from active_file_change_facts — no new tooling",
            "hotspot_definition": "files with most changes in the window, annotated by centrality and guardrail signals",
            "centrality": "heuristic based on path containing core/src/lib/main/cli/api/gateway/ingest/server/daemon/schema/db/storage/repository/sdk/client/protocol",
            "guardrail": "heuristic based on path containing test/ci/lint/type/benchmark/config/nix patterns",
            "caveat": "path-based heuristics are approximate; centrality and guardrail flags are signals, not ground truth",
        },
        "inputs": {
            "active_file_change_facts": str(file_changes_file or "active_file_change_facts.json"),
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
        },
        "projects": project_rows,
        "summary": _hotspot_summary(project_rows),
    }


def build_active_guardrails(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build quality guardrail movement facts from file changes and project snapshots."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    changes_payload = _load_payload(
        file_changes_file or resolve_analysis_path("active_file_change_facts.json"),
        label="active file-change facts",
    )
    snapshot_payload = _load_payload(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )

    file_changes = _list(changes_payload, "file_changes")
    selected = set(projects or ())

    per_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    per_project_cats: dict[str, Counter[str]] = defaultdict(Counter)

    for row in file_changes:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        category = str(row.get("category") or "other")
        per_project_cats[project][category] += 1
        if _is_guardrail(path, category):
            per_project[project].append({
                "path": path,
                "category": category,
                "date": row.get("date"),
                "sha": row.get("short_sha"),
                "subject": row.get("subject"),
                "conventional_kind": row.get("conventional_kind"),
            })

    project_rows: list[dict[str, Any]] = []
    for project_name in sorted(set(per_project_cats) | set(per_project)):
        guardrail_changes = per_project.get(project_name, [])
        cat_counts = per_project_cats.get(project_name, Counter())
        gate_info = _gates_for(snapshot_payload, project_name)
        current_gates = set(gate_info.get("gates", []))

        guardrail_by_type: dict[str, list[str]] = defaultdict(list)
        for change in guardrail_changes:
            guardrail_by_type[change["category"]].append(change["path"])

        test_moved = bool(guardrail_by_type.get("test"))
        ci_moved = bool(guardrail_by_type.get("ci"))
        type_moved = bool(guardrail_by_type.get("type"))
        lint_moved = bool(guardrail_by_type.get("lint"))
        nix_moved = bool(guardrail_by_type.get("nix")) or bool(guardrail_by_type.get("config"))

        holes: list[str] = []
        if test_moved and "pytest" not in current_gates and "cargo test" not in current_gates:
            holes.append("test files changed but no test runner detected in project gates")
        if ci_moved and not any("ci" in g.lower() or "github actions" in g.lower() for g in current_gates):
            holes.append("CI files changed but no CI gate detected")
        if type_moved and not any("mypy" in g.lower() for g in current_gates):
            holes.append("type files changed but no mypy gate detected")

        project_rows.append({
            "project": project_name,
            "guardrail_change_count": len(guardrail_changes),
            "guardrail_changes_by_type": {
                cat: len(set(paths)) for cat, paths in guardrail_by_type.items()
            },
            "top_guardrail_changes": guardrail_changes[:15],
            "quality_gates": sorted(current_gates),
            "quality_gate_count": len(current_gates),
            "test_files_changed": len(set(guardrail_by_type.get("test", []))),
            "ci_files_changed": len(set(guardrail_by_type.get("ci", []))),
            "type_files_changed": len(set(guardrail_by_type.get("type", []))),
            "lint_files_changed": len(set(guardrail_by_type.get("lint", []))),
            "guardrail_holes": holes,
            "interpretation": {
                "guardrail_moved": test_moved or ci_moved or type_moved or lint_moved or nix_moved,
                "tests_moved_with_runner": test_moved and ("pytest" in current_gates or "cargo test" in current_gates),
                "gates_detected": len(current_gates) > 0,
            },
            "caveats": _guardrail_caveats(current_gates, cat_counts),
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "aggregated from active_file_change_facts and active_project_snapshot quality gates",
            "guardrail_definition": "files under test/ci/lint/type/benchmark/config/nix paths or categories",
            "hole_detection": "guardrail file changes without corresponding project gate = potential guardrail debt",
            "caveat": "path-based guardrail detection is approximate; a 'test' file change could be a docs update in a test file",
        },
        "inputs": {
            "active_file_change_facts": str(file_changes_file or "active_file_change_facts.json"),
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
        },
        "projects": project_rows,
        "summary": _guardrail_summary(project_rows),
    }


def run_active_hotspots(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_hotspots(
        start=start, end=end, projects=projects,
        file_changes_file=file_changes_file, snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def run_active_guardrails(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_guardrails(
        start=start, end=end, projects=projects,
        file_changes_file=file_changes_file, snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _load_payload(path: str | PathLike[str], *, label: str) -> dict[str, Any]:
    return load_json_object(path, label=label)


def _list(payload: dict[str, Any] | None, key: str) -> list[Any]:
    if payload is None:
        return []
    result = payload.get(key)
    return result if isinstance(result, list) else []


def _ranked(counter: Counter[str], n: int) -> list[tuple[str, int]]:
    return [(k, v) for k, v in counter.most_common(n) if v > 0]


def _is_central(path: str) -> bool:
    lower = path.lower()
    return any(signal in lower for signal in _CENTRAL_PATH_SIGNALS)


def _is_guardrail(path: str, category: str) -> bool:
    if category in _GUARDRAIL_CATEGORIES:
        return True
    lower = path.lower()
    return any(pattern.lower() in lower for pattern in _GUARDRAIL_PATH_PATTERNS)


def _category_for(path: str, top_cats: list[tuple[str, int]]) -> str:
    lower = path.lower()
    if any(p in lower for p in ("test", "tests", "spec", "conftest")):
        return "test"
    return "other"


def _path_signals(path: str) -> list[str]:
    lower = path.lower()
    signals: list[str] = []
    if any(s in lower for s in _CENTRAL_PATH_SIGNALS):
        signals.append("central")
    if any(p.lower() in lower for p in _GUARDRAIL_PATH_PATTERNS):
        signals.append("guardrail")
    return signals


def _gates_for(snapshot: dict[str, Any] | None, project: str) -> dict[str, Any]:
    if snapshot is None:
        return {}
    projects = snapshot.get("projects")
    if not isinstance(projects, list):
        return {}
    for row in projects:
        if not isinstance(row, dict):
            continue
        if row.get("project") == project:
            gates = row.get("quality_gates")
            if isinstance(gates, (list, tuple)):
                return {"gates": [str(g) for g in gates], "count": len(gates)}
            return {}
    return {}


def _hotspot_caveats(cat_counts: Counter[str], file_counts: Counter[str]) -> list[str]:
    caveats: list[str] = []
    if not file_counts:
        caveats.append("no file changes in window")
    if cat_counts.get("generated") or cat_counts.get("data") or cat_counts.get("vendor"):
        caveats.append("some changes are in generated/data/vendor paths")
    return caveats


def _guardrail_caveats(gates: set[str], cat_counts: Counter[str]) -> list[str]:
    caveats: list[str] = []
    if not gates:
        caveats.append("no quality gates detected in project snapshot")
    if not cat_counts:
        caveats.append("no file changes in window")
    return caveats


def _hotspot_summary(project_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_files = 0
    central_count = 0
    guardrail_count = 0
    for row in project_rows:
        total_files += int(row.get("changed_file_count") or 0)
        central_count += int(row.get("central_files_changed") or 0)
        guardrail_count += int(row.get("guardrail_files_changed") or 0)
    return {
        "project_count": len(project_rows),
        "total_changed_files": total_files,
        "central_files_changed": central_count,
        "guardrail_files_changed": guardrail_count,
        "top_hotspot_projects": sorted(
            ({"project": r["project"], "changed_file_count": r["changed_file_count"],
              "central_files_changed": r["central_files_changed"]} for r in project_rows),
            key=lambda x: -x["changed_file_count"],
        )[:8],
    }


def _guardrail_summary(project_rows: list[dict[str, Any]]) -> dict[str, Any]:
    projects_with_holes = []
    total_guardrail_changes = 0
    for row in project_rows:
        total_guardrail_changes += int(row.get("guardrail_change_count") or 0)
        holes = row.get("guardrail_holes")
        if isinstance(holes, list) and holes:
            projects_with_holes.append({
                "project": row["project"],
                "holes": holes,
            })
    return {
        "project_count": len(project_rows),
        "total_guardrail_changes": total_guardrail_changes,
        "projects_with_guardrail_holes": projects_with_holes,
        "projects_with_detected_gates": sum(
            1 for r in project_rows if r.get("quality_gate_count", 0) > 0
        ),
    }


__all__ = [
    "build_active_hotspots",
    "build_active_guardrails",
    "run_active_hotspots",
    "run_active_guardrails",
]
