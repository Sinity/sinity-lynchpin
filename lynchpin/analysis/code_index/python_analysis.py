"""Python complexity and import-graph analysis for active projects.

Produces ``active_python_complexity.json`` and ``active_python_import_graph.json``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from datetime import date, datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any, Sequence

from ..core.io import load_json_if_exists, resolve_analysis_path, save_json

_IGNORED_PATH_PARTS = {
    ".agent",
    ".direnv",
    ".git",
    ".lynchpin",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "result",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _IGNORED_PATH_PARTS for part in p.relative_to(root).parts)
    )


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


def _module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _source_package(module_name: str, path: Path) -> str:
    if path.name == "__init__.py":
        return module_name
    if "." not in module_name:
        return ""
    return module_name.rsplit(".", 1)[0]


def _relative_import_base(source_package: str, level: int, module: str | None) -> str:
    parts = source_package.split(".") if source_package else []
    keep = max(0, len(parts) - level + 1)
    base_parts = parts[:keep]
    if module:
        base_parts.extend(part for part in module.split(".") if part)
    return ".".join(base_parts)


def _best_internal_target(candidate: str, modules: set[str]) -> str | None:
    parts = [part for part in candidate.split(".") if part]
    while parts:
        name = ".".join(parts)
        if name in modules:
            return name
        parts.pop()
    return None


def _internal_import_targets(source_module: str, source_package: str, tree: ast.AST, modules: set[str]) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _best_internal_target(alias.name, modules)
                if target and target != source_module:
                    targets.add(target)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_import_base(source_package, node.level, node.module)
            else:
                base = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    target = _best_internal_target(base, modules)
                else:
                    child = f"{base}.{alias.name}" if base else alias.name
                    target = _best_internal_target(child, modules)
                    if target != child:
                        target = _best_internal_target(base, modules) if base else target
                if target and target != source_module:
                    targets.add(target)
    return targets


def _cycle_modules(adjacency: dict[str, set[str]]) -> list[str]:
    cycle_nodes: set[str] = set()
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            if node in stack:
                cycle_nodes.update(stack[stack.index(node):])
            return
        visiting.add(node)
        stack.append(node)
        for child in sorted(adjacency.get(node, ())):
            visit(child)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for module in sorted(adjacency):
        visit(module)
    return sorted(cycle_nodes)


def _raw_counts(code: str) -> dict[str, int]:
    lines = code.splitlines()
    blank = 0
    comments = 0
    sloc = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif stripped.startswith("#"):
            comments += 1
        else:
            sloc += 1
    return {
        "loc": len(lines),
        "lloc": sloc,
        "sloc": sloc,
        "comments": comments,
        "blank": blank,
    }


def _complexity_rank(complexity: int) -> str:
    if complexity <= 5:
        return "A"
    if complexity <= 10:
        return "B"
    if complexity <= 20:
        return "C"
    if complexity <= 30:
        return "D"
    if complexity <= 40:
        return "E"
    return "F"


def _node_complexity(node: ast.AST) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp):
            complexity += max(0, len(child.values) - 1)
        elif isinstance(child, ast.Try):
            complexity += len(child.handlers)
            complexity += 1 if child.orelse else 0
            complexity += 1 if child.finalbody else 0
        elif isinstance(child, ast.Match):
            complexity += len(child.cases)
        elif isinstance(child, (
            ast.Assert,
            ast.AsyncFor,
            ast.AsyncWith,
            ast.ExceptHandler,
            ast.For,
            ast.If,
            ast.IfExp,
            ast.While,
            ast.With,
        )):
            complexity += 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += len(child.generators)
    return complexity


def _function_rows(tree: ast.AST) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = _node_complexity(node)
            rows.append({
                "name": node.name,
                "line": node.lineno,
                "complexity": complexity,
                "rank": _complexity_rank(complexity),
                "type": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
            })
    return sorted(rows, key=lambda row: (int(row["line"]), str(row["name"])))


def build_active_python_complexity(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    """Per-file native Python complexity across active projects."""
    snapshot_path = resolve_analysis_path(snapshot_file)
    selected = set(projects) if projects else None
    python_projects, _project_python_set = _python_project_paths(snapshot_path, selected)

    project_rows: list[dict[str, Any]] = []

    for name, abs_path in sorted(python_projects.items()):
        root = Path(abs_path)
        py_files = _python_files(root)
        file_rows: list[dict[str, Any]] = []
        total_loc = total_lloc = total_sloc = total_comments = total_blank = 0
        total_functions = 0
        rank_counts: dict[str, int] = defaultdict(int)
        parse_errors: list[dict[str, str]] = []

        for fpath in py_files:
            try:
                code = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            raw = _raw_counts(code)
            loc = raw["loc"]
            lloc = raw["lloc"]
            sloc = raw["sloc"]
            comments = raw["comments"]
            blank = raw["blank"]
            total_loc += loc
            total_lloc += lloc
            total_sloc += sloc
            total_comments += comments
            total_blank += blank

            rel = str(fpath.relative_to(root))
            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                funcs: list[dict[str, Any]] = []
                parse_errors.append({"path": rel, "error": str(exc)})
            else:
                funcs = _function_rows(tree)
            for function in funcs:
                total_functions += 1
                rank = str(function["rank"])
                rank_counts[rank] += 1

            file_rows.append({"path": rel, "raw": raw, "mi": None, "functions": funcs})

        complex_funcs = sum(1 for row in file_rows for f in row["functions"] if f["complexity"] > 10)
        project_rows.append({
            "project": name, "path": abs_path, "file_count": len(file_rows),
            "summary": {
                "total_loc": total_loc, "total_lloc": total_lloc, "total_sloc": total_sloc,
                "total_comments": total_comments, "total_blank": total_blank,
                "total_functions": total_functions, "complex_functions": complex_funcs,
                "avg_mi": None, "rank_distribution": dict(rank_counts),
            },
            "parse_errors": parse_errors,
            "files": file_rows,
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None},
        "tool_run": {"native_ast": {"available": True, "parser": "ast"}},
        "methodology": {
            "scope": "raw line counts + AST function complexity over active project Python files",
            "complexity": "decision-count approximation; structural signal, not radon parity",
        },
        "projects": project_rows,
    }


def build_active_python_import_graph(
    *,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    """Per-project internal import graph via Python AST."""
    snapshot_path = resolve_analysis_path(snapshot_file)
    selected = set(projects) if projects else None
    python_projects, _project_python_set = _python_project_paths(snapshot_path, selected)

    project_rows: list[dict[str, Any]] = []

    for name, abs_path in sorted(python_projects.items()):
        root = Path(abs_path)
        py_files = _python_files(root)
        module_paths = {
            _module_name(root, path): path
            for path in py_files
            if _module_name(root, path)
        }
        modules = set(module_paths)
        row: dict[str, Any] = {
            "project": name, "path": abs_path,
            "tool_run": {"native_ast": {"available": True, "parser": "ast"}},
        }

        adjacency: dict[str, set[str]] = {module: set() for module in modules}
        parse_errors: list[dict[str, str]] = []
        for module, path in sorted(module_paths.items()):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:
                parse_errors.append({"module": module, "error": str(exc)})
                continue
            source_package = _source_package(module, path)
            adjacency[module] = _internal_import_targets(module, source_package, tree, modules)

        fan_out = {module: len(children) for module, children in adjacency.items()}
        fan_in: dict[str, int] = defaultdict(int)
        for children in adjacency.values():
            for child in children:
                fan_in[child] += 1

        top_fan_out = sorted(fan_out.items(), key=lambda kv: -kv[1])[:10]
        top_fan_in = sorted(fan_in.items(), key=lambda kv: -kv[1])[:10]
        total_imports = sum(fan_out.values())
        cycle_modules = _cycle_modules(adjacency)

        row.update({
            "module_count": len(modules),
            "import_edge_count": total_imports,
            "cycle_modules": cycle_modules,
            "top_fan_out": [{"module": m, "count": c} for m, c in top_fan_out],
            "top_fan_in": [{"module": m, "count": c} for m, c in top_fan_in],
            "parse_errors": parse_errors,
            "modules": [
                {
                    "name": m,
                    "fan_out": fan_out.get(m, 0),
                    "fan_in": fan_in.get(m, 0),
                    "imports": sorted(adjacency.get(m, ())),
                }
                for m in sorted(modules)
            ],
        })
        project_rows.append(row)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tool_run": {"native_ast": {"available": True, "parser": "ast"}},
        "methodology": {"scope": "internal Python import graph from AST import statements"},
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
