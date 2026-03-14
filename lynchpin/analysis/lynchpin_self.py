"""Lynchpin self-analysis — module/LoC breakdown, import graph, coverage maps."""

from __future__ import annotations

import ast
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class ModuleStats:
    subpackage: str
    files: int
    code_lines: int
    blank_lines: int
    comment_lines: int


@dataclass
class ImportEdge:
    source: str
    target: str


@dataclass
class WarehouseCoverage:
    module: str
    has_warehouse_spec: bool


@dataclass
class TestCoverage:
    source_file: str
    has_test: bool
    test_file: Optional[str]


@dataclass
class LynchpinSelfMetrics:
    total_files: int
    total_code_lines: int
    subpackages: List[ModuleStats]
    import_edges: List[ImportEdge]
    warehouse_coverage: List[WarehouseCoverage]
    test_coverage: List[TestCoverage]
    isolation_warnings: List[str]


_LYNCHPIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LYNCHPIN_ROOT.parent

_SUBPACKAGES = [
    "sources",
    "views",
    "metrics",
    "analysis",
    "orchestration",
    "system",
    "core",
    "ingest",
]


def _count_lines(path: Path) -> tuple[int, int, int]:
    """Return (code, blank, comment) line counts."""
    code = blank = comment = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    blank += 1
                elif stripped.startswith("#"):
                    comment += 1
                else:
                    code += 1
    except OSError:
        pass
    return code, blank, comment


def _module_breakdown() -> List[ModuleStats]:
    """Compute LoC breakdown by subpackage."""
    stats: Dict[str, ModuleStats] = {}
    for subpkg in _SUBPACKAGES:
        pkg_dir = _LYNCHPIN_ROOT / subpkg
        if not pkg_dir.is_dir():
            continue
        files = 0
        total_code = total_blank = total_comment = 0
        for py_file in pkg_dir.rglob("*.py"):
            files += 1
            code, blank, comment = _count_lines(py_file)
            total_code += code
            total_blank += blank
            total_comment += comment
        stats[subpkg] = ModuleStats(
            subpackage=subpkg,
            files=files,
            code_lines=total_code,
            blank_lines=total_blank,
            comment_lines=total_comment,
        )
    # Also count top-level files
    top_files = list(_LYNCHPIN_ROOT.glob("*.py"))
    if top_files:
        total_code = total_blank = total_comment = 0
        for py_file in top_files:
            code, blank, comment = _count_lines(py_file)
            total_code += code
            total_blank += blank
            total_comment += comment
        stats["__root__"] = ModuleStats(
            subpackage="__root__",
            files=len(top_files),
            code_lines=total_code,
            blank_lines=total_blank,
            comment_lines=total_comment,
        )
    return list(stats.values())


def _extract_imports(py_file: Path) -> Set[str]:
    """Extract lynchpin import targets from a Python file."""
    imports: Set[str] = set()
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "lynchpin" in node.module:
                # Normalize to subpackage level
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.add(parts[1] if parts[0] == "lynchpin" else parts[0])
            elif node.module.startswith("."):
                # Relative imports — resolve from file location
                pass
            elif node.level and node.level > 0:
                pass
    return imports


def _import_graph() -> List[ImportEdge]:
    """Build cross-subpackage import graph."""
    edges: Set[tuple[str, str]] = set()
    for subpkg in _SUBPACKAGES:
        pkg_dir = _LYNCHPIN_ROOT / subpkg
        if not pkg_dir.is_dir():
            continue
        for py_file in pkg_dir.rglob("*.py"):
            targets = _extract_imports(py_file)
            for target in targets:
                if target != subpkg and target in _SUBPACKAGES:
                    edges.add((subpkg, target))
    return [ImportEdge(source=s, target=t) for s, t in sorted(edges)]


def _warehouse_coverage() -> List[WarehouseCoverage]:
    """Check which source modules have warehouse specs."""
    # Known warehouse sources from warehouse.py
    warehoused = {
        "activitywatch", "atuin", "chatlog", "codex", "dendron", "finance",
        "fbmessenger", "gitstats", "goodreads", "health", "instrumentation",
        "polylogue", "raindrop", "reddit", "sessions", "sleep", "spotify",
        "substack", "takeout", "webhistory", "webhistory_raw", "wykop",
        "analysis",
    }
    results = []
    sources_dir = _LYNCHPIN_ROOT / "sources"
    if sources_dir.is_dir():
        for subdir in ["captures", "exports", "indices", "libraries"]:
            pkg = sources_dir / subdir
            if not pkg.is_dir():
                continue
            for py_file in pkg.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                module_name = py_file.stem
                results.append(WarehouseCoverage(
                    module=f"sources.{subdir}.{module_name}",
                    has_warehouse_spec=module_name in warehoused,
                ))
    return results


def _test_coverage() -> List[TestCoverage]:
    """Map source files to test files."""
    tests_dir = _REPO_ROOT / "tests"
    test_files = {f.stem: f for f in tests_dir.glob("test_*.py")} if tests_dir.is_dir() else {}

    results = []
    for py_file in _LYNCHPIN_ROOT.rglob("*.py"):
        if py_file.name.startswith("_"):
            continue
        rel = py_file.relative_to(_LYNCHPIN_ROOT)
        stem = py_file.stem
        # Check common test naming patterns
        test_name = f"test_{stem}"
        test_name_alt = f"test_{rel.parent.name}_{stem}"
        has_test = test_name in test_files or test_name_alt in test_files
        matched = test_files.get(test_name) or test_files.get(test_name_alt)
        results.append(TestCoverage(
            source_file=str(rel),
            has_test=has_test,
            test_file=str(matched.relative_to(_REPO_ROOT)) if matched else None,
        ))
    return results


def _detect_isolation() -> List[str]:
    """Detect modules that don't import from other lynchpin subpackages."""
    warnings = []
    graph = _import_graph()
    importing = {edge.source for edge in graph}
    imported_by = {edge.target for edge in graph}
    for subpkg in _SUBPACKAGES:
        pkg_dir = _LYNCHPIN_ROOT / subpkg
        if not pkg_dir.is_dir():
            continue
        if subpkg not in importing and subpkg not in imported_by:
            warnings.append(f"{subpkg}: fully isolated (no cross-subpackage imports)")
        elif subpkg not in imported_by:
            warnings.append(f"{subpkg}: not imported by any other subpackage")
    return warnings


def run_self_analysis(out_file: Optional[str] = None) -> LynchpinSelfMetrics:
    """Run full self-analysis and optionally write to JSON."""
    subpackages = _module_breakdown()
    import_edges = _import_graph()
    warehouse_cov = _warehouse_coverage()
    test_cov = _test_coverage()
    isolation = _detect_isolation()

    metrics = LynchpinSelfMetrics(
        total_files=sum(s.files for s in subpackages),
        total_code_lines=sum(s.code_lines for s in subpackages),
        subpackages=subpackages,
        import_edges=import_edges,
        warehouse_coverage=warehouse_cov,
        test_coverage=test_cov,
        isolation_warnings=isolation,
    )

    if out_file:
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(asdict(metrics), f, indent=2)

    return metrics
