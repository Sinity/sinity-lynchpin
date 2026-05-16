"""Python dependency hygiene via pip-audit.

Closes the gap left by Phase 4d (cargo-audit for Rust): runs
``pip-audit --strict --format json`` against each active Python project
directory (or ``requirements.txt`` fallback), parses advisories,
and cross-references against ``active_python_import_graph.json`` to
mark each advisory as direct or transitive.

Hard invariants:

- Never modify ``pyproject.toml`` / ``requirements.txt`` / lockfiles.
- If pip-audit is absent, surface a source-readiness caveat rather than
  silently returning empty findings.
- If a project has neither pyproject nor requirements, skip it (do not
  treat as a failure).
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ..core.io import load_json_if_exists, resolve_analysis_path, save_json

_TIMEOUT_S = 300
_PYPROJECT = "pyproject.toml"
_REQUIREMENTS_NAMES = ("requirements.txt", "requirements.lock", "requirements-dev.txt")
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


def build_active_python_dependency_hygiene(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    import_graph_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot = _dict(load_json_if_exists(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json")))
    import_graph = _dict(load_json_if_exists(
        import_graph_file or resolve_analysis_path("active_python_import_graph.json")))

    selected = set(projects or ())
    snapshot_projects = _project_paths(snapshot, selected)
    project_internal_modules = _internal_module_index(import_graph)

    audit_path = shutil.which("pip-audit")

    pack_caveats: list[str] = []
    if audit_path is None:
        pack_caveats.append(
            "pip-audit not found on PATH; PyPI advisories unavailable"
        )

    rows: list[dict[str, Any]] = []
    for name, root in sorted(snapshot_projects.items()):
        path = Path(root)
        manifest, kind = _detect_python_manifest(path)
        if manifest is None:
            continue
        direct_deps = _direct_dependency_names(path, manifest, kind)
        audit_block = (
            _run_audit(audit_path=audit_path, manifest=manifest, kind=kind)
            if audit_path else
            {"available": False, "reason": "pip-audit not on PATH"}
        )
        imported_modules = _external_import_modules(path, project_internal_modules.get(name, set()))
        annotated = _annotate_advisories(
            audit_block.get("advisories") or [],
            direct_deps=direct_deps,
            imported_modules=imported_modules,
        )
        audit_block["advisories"] = annotated
        rows.append({
            "project": name,
            "path": str(path),
            "manifest": manifest.name,
            "manifest_kind": kind,
            "direct_dependency_count": len(direct_deps),
            "observed_external_import_count": len(imported_modules),
            "observed_external_imports": sorted(imported_modules)[:50],
            "audit": audit_block,
            "caveats": [
                "pip-audit consults the OSV/PyPI advisory feed; freshly disclosed CVEs may "
                "not yet appear",
                "direct vs transitive marking compares advisory package against the manifest's "
                "declared dependencies",
                "observed import matching uses normalized top-level import names; packages whose "
                "distribution name differs from import name may be undercounted",
            ],
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "tool": "pip-audit --strict --format json",
            "manifest_priority": "pyproject.toml first, then requirements*.txt",
            "direct_vs_transitive": "advisory.package matched against the manifest's direct "
                                    "dependency name list",
            "observed_imports": "Python AST top-level imports, excluding internal modules "
                                "and generated/cache directories",
            "lockfile_safety": "pip-audit invocation is read-only; never mutates the manifest",
        },
        "projects": rows,
        "caveats": pack_caveats,
    }


def run_active_python_dependency_hygiene(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    import_graph_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_python_dependency_hygiene(
        start=start, end=end, projects=projects,
        snapshot_file=snapshot_file, import_graph_file=import_graph_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _detect_python_manifest(root: Path) -> tuple[Path | None, str | None]:
    pyproject = root / _PYPROJECT
    if pyproject.exists():
        return pyproject, "pyproject"
    for name in _REQUIREMENTS_NAMES:
        candidate = root / name
        if candidate.exists():
            return candidate, "requirements"
    return None, None


def _direct_dependency_names(root: Path, manifest: Path, kind: str | None) -> set[str]:
    if kind == "pyproject":
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return set()
        names: set[str] = set()
        project = data.get("project") or {}
        for entry in project.get("dependencies") or []:
            names.add(_normalize_pep508_name(entry))
        optional = project.get("optional-dependencies") or {}
        if isinstance(optional, dict):
            for entries in optional.values():
                for entry in entries or []:
                    names.add(_normalize_pep508_name(entry))
        # uv / poetry / hatch dependency-groups also count as direct
        groups = data.get("dependency-groups") or {}
        if isinstance(groups, dict):
            for entries in groups.values():
                for entry in entries or []:
                    names.add(_normalize_pep508_name(entry))
        return {n for n in names if n}
    if kind == "requirements":
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            return set()
        names = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            names.add(_normalize_pep508_name(stripped))
        return {n for n in names if n}
    return set()


def _normalize_pep508_name(spec: str) -> str:
    # Strip extras "[..]", version specifiers, environment markers, comments.
    head = spec.split(";", 1)[0].split("#", 1)[0].strip()
    for delim in ("[", "(", "<", ">", "=", "!", "~", " "):
        idx = head.find(delim)
        if idx != -1:
            head = head[:idx]
    return head.strip().lower().replace("_", "-")


def _internal_module_index(import_graph: dict[str, Any]) -> dict[str, set[str]]:
    """Return {project -> set of module names exposed by that project}."""
    out: dict[str, set[str]] = {}
    rows = import_graph.get("projects")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = row.get("project")
        if not project:
            continue
        modules: set[str] = set()
        for module_row in row.get("modules") or []:
            if isinstance(module_row, dict):
                name = module_row.get("name")
                if isinstance(name, str):
                    modules.add(name)
                    if "." in name:
                        modules.add(name.split(".", 1)[0])
        out[project] = modules
    return out


def _python_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _IGNORED_PATH_PARTS for part in p.relative_to(root).parts)
    )


def _external_import_modules(root: Path, internal_modules: set[str]) -> set[str]:
    internal_top_level = {module.split(".", 1)[0] for module in internal_modules}
    imports: set[str] = set()
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".", 1)[0]
                    if top_level and top_level not in internal_top_level:
                        imports.add(_normalize_import_name(top_level))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top_level = node.module.split(".", 1)[0]
                if top_level and top_level not in internal_top_level:
                    imports.add(_normalize_import_name(top_level))
    return imports


def _normalize_import_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _run_audit(*, audit_path: str, manifest: Path, kind: str | None) -> dict[str, Any]:
    cmd = [audit_path, "--strict", "--format", "json", "--progress-spinner", "off"]
    if kind == "pyproject":
        cmd.append(str(manifest.parent))
    else:
        cmd.extend(["--requirement", str(manifest)])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    output = result.stdout or "{}"
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "reason": f"json parse failed: {exc}",
            "stderr_sample": result.stderr[:500] if result.stderr else "",
        }

    advisories: list[dict[str, Any]] = []
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else None
    if isinstance(dependencies, list):
        for dep in dependencies:
            if not isinstance(dep, dict):
                continue
            pkg = dep.get("name")
            installed = dep.get("version")
            for vuln in dep.get("vulns") or []:
                if not isinstance(vuln, dict):
                    continue
                advisories.append({
                    "id": vuln.get("id"),
                    "package": pkg,
                    "installed": installed,
                    "fix_versions": vuln.get("fix_versions") or [],
                    "description": (vuln.get("description") or "")[:300],
                    "aliases": vuln.get("aliases") or [],
                })
    return {
        "available": True,
        "command": cmd,
        "returncode": result.returncode,
        "advisory_count": len(advisories),
        "advisories": advisories,
        "stderr_sample": result.stderr[:500] if result.stderr else "",
    }


def _annotate_advisories(
    advisories: list[dict[str, Any]],
    *,
    direct_deps: set[str],
    imported_modules: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for adv in advisories:
        package = (adv.get("package") or "").lower().replace("_", "-")
        adv_copy = dict(adv)
        adv_copy["direct"] = package in direct_deps
        adv_copy["transitive"] = not adv_copy["direct"]
        adv_copy["observed_import"] = package in imported_modules
        out.append(adv_copy)
    return out


def _project_paths(snapshot: dict[str, Any], selected: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = snapshot.get("projects")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        if path:
            out[project] = path
    return out


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "build_active_python_dependency_hygiene",
    "run_active_python_dependency_hygiene",
]
