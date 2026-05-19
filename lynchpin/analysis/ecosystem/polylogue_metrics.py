"""Live polylogue repo + archive analysis for ecosystem dashboards."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lynchpin.analysis.core.git import run_git

from ...core.config import get_config
from ..core.textshape import compute_repetition_metrics
from ..core.io import save_json

SKIP_DIRS = {
    ".git",
    ".direnv",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".local",
    ".cache",
}


@dataclass
class FileScan:
    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    functions: int = 0
    classes: int = 0
    async_functions: int = 0
    import_count: int = 0
    control_nodes: int = 0
    type_abstractions: int = 0
    error_handling_nodes: int = 0
    imports: tuple[str, ...] = ()


def _run_git(repo: Path, *args: str) -> str | None:
    return run_git(repo, *args)


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _scan_file(path: Path) -> FileScan:
    code_lines = comment_lines = blank_lines = 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return FileScan()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            blank_lines += 1
        elif stripped.startswith("#"):
            comment_lines += 1
        else:
            code_lines += 1

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return FileScan(code_lines=code_lines, comment_lines=comment_lines, blank_lines=blank_lines)

    imports: set[str] = set()
    functions = classes = async_functions = import_count = control_nodes = type_abstractions = error_handling_nodes = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions += 1
        elif isinstance(node, ast.AsyncFunctionDef):
            functions += 1
            async_functions += 1
        elif isinstance(node, ast.ClassDef):
            classes += 1
            type_abstractions += 1
        elif isinstance(node, ast.Try):
            error_handling_nodes += 1
        elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Match)):
            control_nodes += 1
        elif isinstance(node, ast.Import):
            import_count += len(node.names)
            for alias in node.names:
                if alias.name.startswith("polylogue"):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            import_count += len(node.names)
            if node.module and node.module.startswith("polylogue"):
                imports.add(node.module)
    return FileScan(
        code_lines=code_lines,
        comment_lines=comment_lines,
        blank_lines=blank_lines,
        functions=functions,
        classes=classes,
        async_functions=async_functions,
        import_count=import_count,
        control_nodes=control_nodes,
        type_abstractions=type_abstractions,
        error_handling_nodes=error_handling_nodes,
        imports=tuple(sorted(imports)),
    )


def _package_area_for(path: Path, package_root: Path) -> str:
    rel = path.relative_to(package_root)
    if len(rel.parts) == 1:
        return "__root__"
    return rel.parts[0]


def _import_area(module_name: str) -> str:
    parts = module_name.split(".")
    if len(parts) <= 1:
        return "__root__"
    if len(parts) == 2:
        return "__root__"
    return parts[1]


def _safe_archive_probe(*, timeout_seconds: int = 20) -> dict[str, Any]:
    script = """
import json
from collections import Counter
from datetime import date, timedelta
from lynchpin.sources.polylogue import archive_stats, day_session_summaries

end = date.today()
start = end - timedelta(days=89)

payload = {
    "archive_stats": None,
    "recent_90d": {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "session_count": 0,
        "total_cost_usd": 0.0,
        "total_messages": 0,
        "total_words": 0,
        "providers": {},
        "projects": {},
        "work_event_breakdown": {},
    },
    "notes": [],
}

try:
    payload["archive_stats"] = archive_stats()
except Exception as exc:
    payload["notes"].append(f"archive_stats failed: {type(exc).__name__}: {exc}")

try:
    days = day_session_summaries(start=start, end=end)
    provider_sessions = Counter()
    project_counter = Counter()
    work_kind_counter = Counter()
    total_cost = total_messages = total_words = total_sessions = 0
    for day in days:
        total_sessions += day.session_count
        total_cost += day.total_cost_usd
        total_messages += day.total_messages
        total_words += day.total_words
        provider_sessions.update(day.providers)
        project_counter.update(day.repos_active)
        work_kind_counter.update(day.work_event_breakdown)
    payload["recent_90d"].update({
        "session_count": total_sessions,
        "total_cost_usd": round(total_cost, 4),
        "total_messages": total_messages,
        "total_words": total_words,
        "providers": dict(provider_sessions.most_common()),
        "projects": dict(project_counter.most_common(20)),
        "work_event_breakdown": dict(work_kind_counter.most_common()),
    })
except Exception as exc:
    payload["notes"].append(f"day_session_summaries failed: {type(exc).__name__}: {exc}")

print(json.dumps(payload))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "archive_stats": None,
            "recent_90d": {
                "window_start": None,
                "window_end": None,
                "session_count": 0,
                "total_cost_usd": 0.0,
                "total_messages": 0,
                "total_words": 0,
                "providers": {},
                "projects": {},
                "work_event_breakdown": {},
            },
            "notes": [f"archive probe timed out after {timeout_seconds}s"],
        }

    if result.returncode != 0:
        return {
            "archive_stats": None,
            "recent_90d": {
                "window_start": None,
                "window_end": None,
                "session_count": 0,
                "total_cost_usd": 0.0,
                "total_messages": 0,
                "total_words": 0,
                "providers": {},
                "projects": {},
                "work_event_breakdown": {},
            },
            "notes": [result.stderr.strip() or "archive probe failed"],
        }
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        return {
            "archive_stats": None,
            "recent_90d": {
                "window_start": None,
                "window_end": None,
                "session_count": 0,
                "total_cost_usd": 0.0,
                "total_messages": 0,
                "total_words": 0,
                "providers": {},
                "projects": {},
                "work_event_breakdown": {},
            },
            "notes": ["archive probe returned non-object JSON"],
        }
    return payload


def _distribution_stats(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    weights = {row["area"]: row["code_lines"] for row in rows if row["code_lines"] > 0}
    total = sum(weights.values())
    if total == 0:
        return {"subsystems": 0, "top1_share": 0.0, "top5_share": 0.0, "entropy_bits": 0.0, "hhi": 0.0}
    shares = sorted((value / total for value in weights.values()), reverse=True)
    import math

    return {
        "subsystems": len(weights),
        "top1_share": round(shares[0], 6),
        "top5_share": round(sum(shares[:5]), 6),
        "entropy_bits": round(-sum(share * math.log2(share) for share in shares if share > 0), 6),
        "hhi": round(sum(share * share for share in shares), 6),
    }


def _readme_text(repo: Path) -> str:
    readme = repo / "README.md"
    if not readme.exists():
        return ""
    try:
        return readme.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _capability_matrix(
    *,
    repo: dict[str, Any],
    areas: list[dict[str, Any]],
    archive_probe: dict[str, Any],
    readme_text: str,
) -> list[dict[str, Any]]:
    area_names = {row["area"].lower() for row in areas}
    readme_lower = readme_text.lower()
    recent = archive_probe.get("recent_90d", {})
    capability_specs = [
        ("archive substrate", "storage" in area_names or "archive" in readme_lower, True, ["archive", "ingest", "sqlite"]),
        ("lexical search", "query" in area_names or "fts" in readme_lower, False, ["search", "fts", "lexical"]),
        ("semantic retrieval", "semantic" in readme_lower or "vector" in readme_lower, True, ["semantic", "vector"]),
        ("derived work products", any(name in area_names for name in ("products", "materialize", "profiles")), True, ["profiles", "summaries", "products", "work events"]),
        ("site publishing", "site" in readme_lower or "render" in area_names, True, ["site", "html archive", "publication"]),
        ("assistant surface", "mcp" in readme_lower or "mcp" in area_names, True, ["mcp", "assistant"]),
        ("python api", repo["classes"] > 0 and "library api" in readme_lower, True, ["python api", "library api"]),
        ("operational analytics", recent.get("session_count", 0) > 0 or "dashboard" in readme_lower, True, ["dashboard", "analytics", "stats"]),
        ("validation/devtools", "devtools" in readme_lower or "devtools" in area_names, True, ["devtools", "validation", "benchmark"]),
    ]
    matrix = []
    for capability, repo_evidence, distinguishes, keywords in capability_specs:
        matrix.append(
            {
                "capability": capability,
                "repo_evidence": bool(repo_evidence),
                "distinguishes_from_ripgrep": distinguishes,
                "mentioned_in_readme": any(keyword in readme_lower for keyword in keywords),
            }
        )
    return matrix


def _diagnostic_scorecard(matrix: list[dict[str, Any]], repo: dict[str, Any], archive_probe: dict[str, Any]) -> list[dict[str, str]]:
    mentioned = sum(1 for row in matrix if row["mentioned_in_readme"])
    evidenced = sum(1 for row in matrix if row["repo_evidence"])
    differentiated = sum(1 for row in matrix if row["repo_evidence"] and row["distinguishes_from_ripgrep"])
    recent_sessions = archive_probe.get("recent_90d", {}).get("session_count", 0)

    def assessment(value: int, *, high: int, medium: int) -> str:
        if value >= high:
            return "high"
        if value >= medium:
            return "medium"
        return "low"

    return [
        {
            "dimension": "surface breadth",
            "assessment": assessment(evidenced, high=7, medium=4),
            "reason": f"{evidenced} code-backed capabilities are visible in the current repo scan.",
        },
        {
            "dimension": "differentiation clarity",
            "assessment": assessment(differentiated, high=5, medium=3),
            "reason": f"{differentiated} capabilities clearly exceed plain grep/archive positioning.",
        },
        {
            "dimension": "README fidelity",
            "assessment": assessment(mentioned, high=6, medium=4),
            "reason": f"{mentioned} capabilities are explicitly surfaced in README messaging.",
        },
        {
            "dimension": "live archive grounding",
            "assessment": assessment(recent_sessions, high=200, medium=25),
            "reason": f"Recent 90-day archive probe found {recent_sessions} profiled sessions.",
        },
        {
            "dimension": "test and maintenance posture",
            "assessment": assessment(repo["test_code_lines"], high=8000, medium=2000),
            "reason": f"{repo['test_code_lines']:,} test LOC and devtools surfaces support verification claims.",
        },
    ]


def _pitch_rewrites(matrix: list[dict[str, Any]]) -> list[dict[str, str]]:
    mentioned = {row["capability"] for row in matrix if row["mentioned_in_readme"]}
    strong = [row["capability"] for row in matrix if row["repo_evidence"] and row["distinguishes_from_ripgrep"]]
    candidates = [cap for cap in strong if cap not in mentioned]
    rewrites = [
        {
            "irc_phrase": "local archive for AI conversation exports",
            "stronger_truthful_rewrite": "local-first AI conversation archive with query surfaces, derived work products, and assistant-ready retrieval",
        },
        {
            "irc_phrase": "searches chats",
            "stronger_truthful_rewrite": "indexes multi-provider chat exports into a searchable archive with lexical filters, optional semantic retrieval, and materialized profiles",
        },
    ]
    if candidates:
        rewrites.append(
            {
                "irc_phrase": "tool for transcripts",
                "stronger_truthful_rewrite": f"archive substrate plus {', '.join(candidates[:3])} instead of a flat transcript pile",
            }
        )
    return rewrites


def run_polylogue_metrics(out_file: str | Path) -> dict[str, Any]:
    repo = Path(get_config().polylogue_root).resolve()
    if repo.name != "polylogue":
        repo = Path("/realm/project/polylogue")
    package_root = repo / "polylogue"
    tests_root = repo / "tests"

    package_files = _iter_python_files(package_root)
    test_files = _iter_python_files(tests_root) if tests_root.exists() else []

    by_area: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"files": 0, "code_lines": 0, "comment_lines": 0, "blank_lines": 0, "functions": 0, "classes": 0, "async_functions": 0}
    )
    inbound: Counter[str] = Counter()
    outbound: Counter[str] = Counter()
    edge_weights: Counter[tuple[str, str]] = Counter()

    total_package_code = total_package_comments = total_package_blank = 0
    total_functions = total_classes = total_async_functions = 0
    total_imports = total_control = total_type_abstractions = total_error_handling = 0
    package_texts: list[str] = []
    test_texts: list[str] = []

    for path in package_files:
        area = _package_area_for(path, package_root)
        scan = _scan_file(path)
        try:
            package_texts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
        row = by_area[area]
        row["files"] += 1
        row["code_lines"] += scan.code_lines
        row["comment_lines"] += scan.comment_lines
        row["blank_lines"] += scan.blank_lines
        row["functions"] += scan.functions
        row["classes"] += scan.classes
        row["async_functions"] += scan.async_functions
        total_package_code += scan.code_lines
        total_package_comments += scan.comment_lines
        total_package_blank += scan.blank_lines
        total_functions += scan.functions
        total_classes += scan.classes
        total_async_functions += scan.async_functions
        total_imports += scan.import_count
        total_control += scan.control_nodes
        total_type_abstractions += scan.type_abstractions
        total_error_handling += scan.error_handling_nodes

        targets = {_import_area(name) for name in scan.imports if _import_area(name) != area}
        for target in targets:
            outbound[area] += 1
            inbound[target] += 1
            edge_weights[(area, target)] += 1

    test_code_lines = 0
    for path in test_files:
        test_code_lines += _scan_file(path).code_lines
        try:
            test_texts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    area_rows = []
    for area, row in sorted(by_area.items(), key=lambda item: item[1]["code_lines"], reverse=True):
        area_rows.append(
            {
                "area": area,
                **row,
                "in_degree": inbound.get(area, 0),
                "out_degree": outbound.get(area, 0),
            }
        )

    archive_probe = _safe_archive_probe()
    readme_text = _readme_text(repo)
    capability_gap_matrix = _capability_matrix(
        repo={
            "classes": total_classes,
            "test_code_lines": test_code_lines,
        },
        areas=area_rows,
        archive_probe=archive_probe,
        readme_text=readme_text,
    )
    scorecard = _diagnostic_scorecard(
        capability_gap_matrix,
        {
            "test_code_lines": test_code_lines,
        },
        archive_probe,
    )
    complexity_density = {
        "defs_per_kloc": round(total_functions / max(total_package_code, 1) * 1000, 4),
        "imports_per_kloc": round(total_imports / max(total_package_code, 1) * 1000, 4),
        "control_per_kloc": round(total_control / max(total_package_code, 1) * 1000, 4),
        "type_abstractions_per_kloc": round(total_type_abstractions / max(total_package_code, 1) * 1000, 4),
        "async_per_kloc": round(total_async_functions / max(total_package_code, 1) * 1000, 4),
        "error_handling_per_kloc": round(total_error_handling / max(total_package_code, 1) * 1000, 4),
        "ui_event_per_kloc": 0.0,
        "property_per_kloc": 0.0,
    }

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo": {
            "path": str(repo),
            "branch": _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "head": _run_git(repo, "rev-parse", "HEAD"),
            "dirty": bool(_run_git(repo, "status", "--short")),
            "python_files": len(package_files),
            "test_python_files": len(test_files),
            "package_code_lines": total_package_code,
            "test_code_lines": test_code_lines,
            "comment_lines": total_package_comments,
            "blank_lines": total_package_blank,
            "functions": total_functions,
            "classes": total_classes,
            "async_functions": total_async_functions,
            "complexity_density": complexity_density,
            "subsystem_distribution": _distribution_stats(area_rows),
            "repetition": {
                "runtime_primary": compute_repetition_metrics(package_texts),
                "whole_python": compute_repetition_metrics(package_texts + test_texts),
            },
            "commit_count": int(_run_git(repo, "rev-list", "--count", "HEAD") or 0),
        },
        "areas": area_rows,
        "architecture": {
            "edge_count": sum(edge_weights.values()),
            "largest_import_hubs_in_degree": sorted(area_rows, key=lambda row: row["in_degree"], reverse=True)[:10],
            "largest_import_hubs_out_degree": sorted(area_rows, key=lambda row: row["out_degree"], reverse=True)[:10],
            "top_runtime_areas_by_loc": area_rows[:10],
            "import_edges": [
                {"source": source, "target": target, "weight": weight}
                for (source, target), weight in edge_weights.most_common(50)
            ],
        },
        "archive": {
            "stats": archive_probe.get("archive_stats"),
            "recent_90d": archive_probe.get("recent_90d"),
            "notes": archive_probe.get("notes", []),
        },
        "capability_gap_matrix": capability_gap_matrix,
        "diagnostic_scorecard": scorecard,
        "pitch_rewrites": _pitch_rewrites(capability_gap_matrix),
        "readme_excerpt": readme_text[:12000],
    }
    save_json(out_file, payload, sort_keys=True)
    return payload
