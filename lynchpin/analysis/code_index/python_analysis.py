"""Radon complexity and grimp import-graph analysis for Python projects.

Produces ``active_python_complexity.json`` and ``active_python_import_graph.json``.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from datetime import date, datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any, Sequence

from ..core.io import load_json_if_exists, resolve_analysis_path, save_json


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if ".venv" not in p.parts and "node_modules" not in p.parts)


def _radon_version() -> str | None:
    try:
        result = subprocess.run(
            ["radon", "--version"], capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def _grimp_version() -> str | None:
    try:
        import grimp
        return getattr(grimp, "__version__", "unknown")
    except ImportError:
        return None


def _python_project_paths(
    snapshot_path: str, selected: set[str] | None,
) -> tuple[dict[str, str], set[str]]:
    """Return {project_name: abs_path} for Python projects from snapshot."""
    snapshot = load_json_if_exists(snapshot_path) or {}
    result: dict[str, str] = {}
    top_exts: dict[str, set[str]] = {}
    for proj in snapshot.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        name = proj.get("project")
        path = proj.get("path")
        if not name or not path or (selected and name not in selected):
            continue
        ext_str = proj.get("dominant_extension") or ""
        exts = {ext_str} if ext_str else set()
        if not exts:
            structure = proj.get("structure") or {}
            extensions = structure.get("extensions") or {}
            exts = {ext.lstrip(".").lower() for ext in extensions.keys()}
        top_exts[name] = exts
        p = Path(path)
        if p.is_dir():
            result[name] = str(p)
    python_projects = {
        name: path for name, path in result.items()
        if name in top_exts and any(ext in {".py", "py"} for ext in top_exts[name])
    }
    if not python_projects:
        python_projects = {name: path for name, path in result.items()
                           if Path(path, ".git").exists()}
    return python_projects, set(python_projects)


def build_active_python_complexity(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    """Per-file radon complexity across Python projects."""
    snapshot_path = resolve_analysis_path(snapshot_file)
    selected = set(projects) if projects else None
    python_projects, _project_python_set = _python_project_paths(snapshot_path, selected)

    version = _radon_version()
    tool_available = version is not None
    project_rows: list[dict[str, Any]] = []

    for name, abs_path in sorted(python_projects.items()):
        root = Path(abs_path)
        py_files = _python_files(root)
        file_rows: list[dict[str, Any]] = []
        total_loc = total_lloc = total_sloc = total_comments = total_blank = 0
        total_functions = 0
        rank_counts: dict[str, int] = defaultdict(int)
        mi_sum = mi_count = 0.0

        if not tool_available:
            project_rows.append({
                "project": name, "path": abs_path, "file_count": len(py_files),
                "tool_run": {"radon": {"available": False, "version": version}},
            })
            continue

        for fpath in py_files:
            try:
                code = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            try:
                import radon.raw
                import radon.complexity
                import radon.metrics
            except ImportError:
                tool_available = False
                break
            raw = radon.raw.analyze(code)
            loc = int(raw.loc)
            lloc = int(raw.lloc)
            sloc = int(raw.sloc)
            comments = int(raw.comments)
            blank = int(raw.blank)
            total_loc += loc
            total_lloc += lloc
            total_sloc += sloc
            total_comments += comments
            total_blank += blank

            try:
                blocks = list(radon.complexity.cc_visit(code))
            except Exception:
                blocks = []
            funcs = []
            for b in blocks:
                total_functions += 1
                rank = radon.complexity.cc_rank(b.complexity)
                rank_counts[rank] += 1
                funcs.append({
                    "name": b.name, "line": b.lineno,
                    "complexity": b.complexity, "rank": rank,
                    "type": getattr(b, "type", "function"),
                })

            rel = str(fpath.relative_to(root))
            try:
                mi = radon.metrics.mi_visit(code, True)
                if mi is not None:
                    mi_sum += mi
                    mi_count += 1
            except Exception:
                mi = None

            file_rows.append({
                "path": rel,
                "raw": {"loc": loc, "lloc": lloc, "sloc": sloc,
                        "comments": comments, "blank": blank},
                "mi": round(mi, 1) if mi is not None else None,
                "functions": funcs,
            })

        avg_mi = round(mi_sum / mi_count, 1) if mi_count > 0 else None
        complex_funcs = sum(1 for row in file_rows for f in row["functions"] if f["complexity"] > 10)
        project_rows.append({
            "project": name, "path": abs_path, "file_count": len(file_rows),
            "summary": {
                "total_loc": total_loc, "total_lloc": total_lloc, "total_sloc": total_sloc,
                "total_comments": total_comments, "total_blank": total_blank,
                "total_functions": total_functions, "complex_functions": complex_funcs,
                "avg_mi": avg_mi, "rank_distribution": dict(rank_counts),
            },
            "files": file_rows,
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None},
        "tool_run": {"radon": {"available": tool_available, "version": version}},
        "methodology": {"scope": "radon raw + cc + mi on all .py files"},
        "projects": project_rows,
    }


def build_active_python_import_graph(
    *,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    """Per-project import graph via grimp."""
    snapshot_path = resolve_analysis_path(snapshot_file)
    selected = set(projects) if projects else None
    python_projects, _project_python_set = _python_project_paths(snapshot_path, selected)

    version = _grimp_version()
    tool_available = version is not None
    project_rows: list[dict[str, Any]] = []

    for name, abs_path in sorted(python_projects.items()):
        root = Path(abs_path)
        pkg_name = root.name
        # Try to find the actual Python package dir
        pkg_dir = root / pkg_name if (root / pkg_name).is_dir() else root
        pkg_dir = next((d for d in root.iterdir() if d.is_dir() and (d / "__init__.py").exists()), None) or pkg_dir

        row: dict[str, Any] = {
            "project": name, "path": abs_path,
            "tool_run": {"grimp": {"available": tool_available, "version": version}},
        }

        if not tool_available:
            project_rows.append(row)
            continue

        import grimp
        try:
            import sys
            sys.path.insert(0, str(root.parent))
            sys.path.insert(0, str(root))
            graph = grimp.build_graph(pkg_name, cache_dir=None)
            sys.path.pop(0)
            sys.path.pop(0)
        except (ValueError, ImportError) as exc:
            row["error"] = str(exc)
            project_rows.append(row)
            continue

        modules = sorted(graph.modules)
        fan_out: dict[str, int] = {}
        fan_in: dict[str, int] = defaultdict(int)
        for mod in modules:
            children = graph.find_children(mod)
            fan_out[mod] = len(children)
            for child in children:
                fan_in[child] += 1

        top_fan_out = sorted(fan_out.items(), key=lambda kv: -kv[1])[:10]
        top_fan_in = sorted(fan_in.items(), key=lambda kv: -kv[1])[:10]
        total_imports = sum(fan_out.values())

        # Detect cycles nominating breakers
        try:
            breakers = graph.nominate_cycle_breakers()
            cycle_modules = sorted(set(b for b in breakers))
        except Exception:
            cycle_modules = []

        row.update({
            "module_count": len(modules),
            "import_edge_count": total_imports,
            "cycle_modules": cycle_modules,
            "top_fan_out": [{"module": m, "count": c} for m, c in top_fan_out],
            "top_fan_in": [{"module": m, "count": c} for m, c in top_fan_in],
            "modules": [
                {"module": m, "fan_out": fan_out.get(m, 0), "fan_in": fan_in.get(m, 0)}
                for m in modules
            ],
        })
        project_rows.append(row)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tool_run": {"grimp": {"available": tool_available, "version": version}},
        "methodology": {"scope": "grimp import graph analysis"},
        "projects": project_rows,
    }


def run_active_python_complexity(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    payload = build_active_python_complexity(
        start=start, end=end, projects=projects, snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def run_active_python_import_graph(
    out_file: str | PathLike[str],
    *,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    payload = build_active_python_import_graph(
        projects=projects, snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


__all__ = [
    "build_active_python_complexity", "run_active_python_complexity",
    "build_active_python_import_graph", "run_active_python_import_graph",
]
