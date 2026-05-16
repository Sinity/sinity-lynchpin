"""Commit-semantic capsules: hunk parsing, symbol mapping, and operation classification.

Produces active_commit_hunks.json and active_commit_semantics.json —
deterministic evidence of what code meaning changed inside each commit.
"""

from __future__ import annotations

import ast
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...substrate.work_commits import read_commit_facts
from ...substrate.connection import connect, substrate_path
from ..core.io import resolve_analysis_path, save_json


_OPERATION_LABELS = (
    "public_api_surface",
    "internal_behavior",
    "data_model_or_schema",
    "persistence_or_materialization",
    "cli_or_user_surface",
    "config_or_deployment",
    "test_or_verification",
    "type_or_lint_guardrail",
    "error_handling_or_diagnostics",
    "performance_or_resource_control",
    "refactor_move_or_extract",
    "deletion_cleanup",
    "documentation_or_narrative",
    "generated_or_mechanical",
    "unknown_mixed",
)

_LINE_CLASS_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "import_or_dep",
        (
            "import ",
            "from ",
            "use ",
            "extern crate",
            "require ",
            "#include",
            "#include",
        ),
    ),
    (
        "fn_sig_or_def",
        ("def ", "class ", "async def", "fn ", "struct ", "enum ", "trait ", "impl "),
    ),
    (
        "test_assert",
        ("assert", "test ", "it(", "describe(", "test!(", "#[test]", "#[cfg(test)]"),
    ),
    (
        "schema_or_field",
        (
            ": str",
            ": int",
            ": float",
            ": bool",
            ": dict",
            ": list",
            "Column(",
            "Field(",
        ),
    ),
    (
        "config_or_option",
        ("config", "settings", "options", "env", "CLI argument", "argparse"),
    ),
    (
        "error_or_log",
        ("raise ", "except ", "log.", "logger.", "error!", "warn!", "info!", "debug!"),
    ),
    (
        "storage_or_query",
        ("execute(", "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "commit(", "save("),
    ),
    ("docs_or_prose", ("#", "//", "/*", "*/", '"""', '"""', "## ", "> ")),
]


def build_active_commit_hunks(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    max_commits: int = 80,
) -> dict[str, Any]:
    """Extract structured diff hunks for top commits in the window."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    with connect(substrate_path()) as conn:
        commit_payload = read_commit_facts(
            conn,
            start=start,
            end=end,
            projects=tuple(projects) if projects else None,
        )
    commits = _list(commit_payload, "commits")
    selected = set(projects or ())

    hunk_rows: list[dict[str, Any]] = []
    commit_count = 0

    for row in commits:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        sha = str(row.get("sha") or "")
        if not sha or len(sha) < 7:
            continue
        if commit_count >= max_commits:
            break
        commit_count += 1

        files = _diff_files(project, sha, row)
        for file_info in files:
            hunk_rows.append(
                {
                    "project": project,
                    "sha": sha,
                    "short_sha": row.get("short_sha"),
                    "date": row.get("date"),
                    "subject": row.get("subject"),
                    "path": file_info.get("path"),
                    "status": file_info.get("status"),
                    "previous_path": file_info.get("previous_path"),
                    "hunk_count": file_info.get("hunk_count", 0),
                    "added_lines": file_info.get("added_lines", 0),
                    "deleted_lines": file_info.get("deleted_lines", 0),
                    "function_context": file_info.get("function_context", []),
                }
            )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "git diff --unified=0 --find-renames for first-parent commits",
            "scope": f"up to {max_commits} commits in the window",
            "caveat": "hunk-level data is structural, not semantic — use commit_semantics for operation classification",
        },
        "commit_count": commit_count,
        "hunk_count": len(hunk_rows),
        "hunks": hunk_rows,
    }


def build_active_commit_semantics(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    max_commits: int = 40,
) -> dict[str, Any]:
    """Classify semantic operations for top commits in the window."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    with connect(substrate_path()) as conn:
        commit_payload = read_commit_facts(
            conn,
            start=start,
            end=end,
            projects=tuple(projects) if projects else None,
        )
    commits = _list(commit_payload, "commits")
    selected = set(projects or ())

    semantic_rows: list[dict[str, Any]] = []
    commit_count = 0

    for row in commits:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        sha = str(row.get("sha") or "")
        if not sha or len(sha) < 7 or commit_count >= max_commits:
            continue
        commit_count += 1

        path = str(row.get("path") or str(Path(project) if project else ""))
        if not path:
            continue
        repo_path = _repo_path(project, row)

        files = _diff_files(project, sha, row)
        symbols = _extract_symbols(repo_path, sha, files, row)
        operations = _classify_operations(files, symbols)
        impact = _impact_assessment(files, symbols, operations)

        semantic_rows.append(
            {
                "project": project,
                "sha": sha,
                "short_sha": row.get("short_sha"),
                "date": row.get("date"),
                "subject": row.get("subject"),
                "conventional_kind": row.get("conventional_kind"),
                "conventional_scope": row.get("conventional_scope"),
                "files_changed": int(row.get("files_changed") or 0),
                "file_count": len(files),
                "symbol_count": len(symbols),
                "symbols": symbols[:30],
                "semantic_operations": operations,
                "impact": impact,
                "risk_flags": _risk_flags(files, symbols, row),
                "caveats": _semantic_caveats(files, symbols),
            }
        )

    operation_totals: Counter[str] = Counter()
    for row in semantic_rows:
        for op, weight in row["semantic_operations"].items():
            operation_totals[op] += weight

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "deterministic heuristics over git diff hunks, Python AST symbols, and file categories",
            "semantic_operation_labels": list(_OPERATION_LABELS),
            "caveat": "operation labels are deterministic heuristics, not proof of user value or correctness",
        },
        "commit_count": commit_count,
        "commits": semantic_rows,
        "summary": {
            "operation_distribution": dict(operation_totals.most_common()),
            "top_commits_by_symbol_count": sorted(
                (
                    {
                        "sha": r["short_sha"],
                        "subject": r["subject"],
                        "symbol_count": r["symbol_count"],
                    }
                    for r in semantic_rows
                ),
                key=lambda x: -x["symbol_count"],
            )[:10],
        },
    }


def run_active_commit_hunks(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_commit_hunks(start=start, end=end, projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def run_active_commit_semantics(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_commit_semantics(start=start, end=end, projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _diff_files(project: str, sha: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    repo_path = _repo_path(project, row)
    if not repo_path:
        return _files_from_row(row)
    parent = _parent_sha(repo_path, sha)
    files: list[dict[str, Any]] = []
    try:
        args = ["diff", "--unified=0", "--find-renames", "--find-copies"]
        if parent:
            args.extend([f"{parent}..{sha}"])
        else:
            args.extend(["--root", sha])
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return _files_from_row(row)
        files = _parse_diff_hunks(result.stdout)
    except (subprocess.TimeoutExpired, OSError):
        return _files_from_row(row)
    return files or _files_from_row(row)


def _parse_diff_hunks(diff_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    added = 0
    deleted = 0
    hunk_count = 0
    functions: list[str] = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git "):
            if current_file is not None:
                current_file["added_lines"] = added
                current_file["deleted_lines"] = deleted
                current_file["hunk_count"] = hunk_count
                current_file["function_context"] = functions[:10]
                files.append(current_file)
            current_file = {"status": "modified", "previous_path": None}
            added = 0
            deleted = 0
            hunk_count = 0
            functions = []
        elif line.startswith("--- ") or line.startswith("+++ "):
            if current_file and line.startswith("+++ "):
                path = line[6:]
                if "\t" in path:
                    path = path.split("\t")[0]
                current_file["path"] = path
        elif line.startswith("rename from "):
            if current_file:
                current_file["previous_path"] = line[15:].strip()
                current_file["status"] = "renamed"
        elif line.startswith("rename to "):
            if current_file:
                current_file["path"] = line[13:].strip()
        elif line.startswith("deleted file"):
            if current_file:
                current_file["status"] = "deleted"
        elif line.startswith("new file"):
            if current_file:
                current_file["status"] = "added"
        elif line.startswith("@@") and current_file:
            hunk_count += 1
            func_match = line.split("@@", 2)[-1].strip()
            if func_match and func_match != current_file.get("path", ""):
                functions.append(func_match)
        elif current_file is not None:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                deleted += 1

    if current_file is not None:
        current_file["added_lines"] = added
        current_file["deleted_lines"] = deleted
        current_file["hunk_count"] = hunk_count
        current_file["function_context"] = functions[:10]
        files.append(current_file)
    return files


def _parent_sha(repo_path: str, sha: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%P", sha],
            capture_output=True,
            text=True,
            timeout=10,
        )
        parents = result.stdout.strip().split()
        return parents[0] if parents else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _extract_symbols(
    repo_path: str,
    sha: str,
    files: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for file_info in files[:15]:
        path = file_info.get("path", "")
        if not path or not path.endswith(".py"):
            continue
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "show", f"{sha}:{path}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                continue
            tree = ast.parse(result.stdout)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        {
                            "kind": "function",
                            "name": node.name,
                            "line": node.lineno,
                            "path": path,
                        }
                    )
                elif isinstance(node, ast.ClassDef):
                    symbols.append(
                        {
                            "kind": "class",
                            "name": node.name,
                            "line": node.lineno,
                            "path": path,
                        }
                    )
        except (SyntaxError, subprocess.TimeoutExpired, OSError):
            continue
    return symbols


def _classify_operations(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> dict[str, float]:
    ops: dict[str, float] = {}
    paths = [f.get("path", "") for f in files]
    if any(
        p and ("test" in p.lower() or "spec" in p.lower() or "conftest" in p.lower())
        for p in paths
    ):
        ops["test_or_verification"] = max(ops.get("test_or_verification", 0), 0.8)
    if any(
        p
        and (
            "cli" in p.lower()
            or "main.py" == p
            or "main.rs" == p
            or "argparse" in p.lower()
        )
        for p in paths
    ):
        ops["cli_or_user_surface"] = max(ops.get("cli_or_user_surface", 0), 0.7)
    if any(
        p
        and ("schema" in p.lower() or "model" in p.lower() or "migration" in p.lower())
        for p in paths
    ):
        ops["data_model_or_schema"] = max(ops.get("data_model_or_schema", 0), 0.7)
    if any(
        p
        and (
            "storage" in p.lower()
            or "db" in p.lower()
            or "repository" in p.lower()
            or "database" in p.lower()
        )
        for p in paths
    ):
        ops["persistence_or_materialization"] = max(
            ops.get("persistence_or_materialization", 0), 0.7
        )
    if any(
        p
        and (
            "config" in p.lower()
            or "settings" in p.lower()
            or "nix" in p.lower()
            or ".toml" in p
            or ".nix" in p
        )
        for p in paths
    ):
        ops["config_or_deployment"] = max(ops.get("config_or_deployment", 0), 0.7)
    if any(
        p
        and (
            "doc" in p.lower()
            or "readme" in p.lower()
            or "changelog" in p.lower()
            or ".md" in p.lower()
        )
        for p in paths
    ):
        ops["documentation_or_narrative"] = max(
            ops.get("documentation_or_narrative", 0), 0.8
        )
    if any(
        p
        and (
            "ci" in p.lower()
            or "lint" in p.lower()
            or "mypy" in p.lower()
            or "type" in p.lower()
        )
        for p in paths
    ):
        ops["type_or_lint_guardrail"] = max(ops.get("type_or_lint_guardrail", 0), 0.7)
    if any(
        p and ("error" in p.lower() or "exception" in p.lower() or "log" in p.lower())
        for p in paths
    ):
        ops["error_handling_or_diagnostics"] = max(
            ops.get("error_handling_or_diagnostics", 0), 0.6
        )
    if any(s["kind"] in ("function", "class") for s in symbols):
        ops["internal_behavior"] = max(ops.get("internal_behavior", 0), 0.5)
        if any(
            "api" in s.get("name", "").lower() or "public" in s.get("name", "").lower()
            for s in symbols
        ):
            ops["public_api_surface"] = max(ops.get("public_api_surface", 0), 0.6)
    if any(f.get("status") == "deleted" for f in files) and not any(
        f.get("status") in ("added", "modified") for f in files
    ):
        ops["deletion_cleanup"] = max(ops.get("deletion_cleanup", 0), 0.9)
    if any(
        "refactor" in p.lower() or "rename" in p.lower() or "move" in p.lower()
        for p in paths
    ):
        ops["refactor_move_or_extract"] = max(
            ops.get("refactor_move_or_extract", 0), 0.6
        )

    if not ops:
        ops["unknown_mixed"] = 0.3
    return ops


def _impact_assessment(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    operations: dict[str, float],
) -> dict[str, str]:
    impact: dict[str, str] = {}
    paths = [f.get("path", "") for f in files]
    if any(
        "api" in p.lower() or "public" in p.lower() or "__init__" in p for p in paths
    ):
        impact["public_api"] = "likely"
    elif any(s["kind"] in ("class", "function") for s in symbols):
        impact["public_api"] = "possible"
    else:
        impact["public_api"] = "none"
    impact["runtime_behavior"] = (
        "likely" if operations.get("internal_behavior", 0) > 0.4 else "possible"
    )
    impact["data_contract"] = (
        "likely" if operations.get("data_model_or_schema", 0) > 0.5 else "none"
    )
    impact["guardrail"] = (
        "likely" if operations.get("test_or_verification", 0) > 0.5 else "possible"
    )
    return impact


def _risk_flags(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    paths = [f.get("path", "") for f in files]
    categories = _counter_dict(row.get("categories"))
    if categories.get("core") or categories.get("src"):
        if len(files) > 5:
            flags.append("large_touch_on_core")
    if any(
        "schema" in p.lower() or "migration" in p.lower() or "storage" in p.lower()
        for p in paths
    ):
        if (
            not any("test" in p.lower() for p in paths)
            and categories.get("test", 0) == 0
        ):
            flags.append("schema_or_storage_change_without_test")
    if any(
        "security" in p.lower() or "auth" in p.lower() or "secret" in p.lower()
        for p in paths
    ):
        flags.append("security_surface_changed")
    return flags[:5]


def _semantic_caveats(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> list[str]:
    caveats: list[str] = []
    non_py = [
        f for f in files if f.get("path", "") and not f.get("path", "").endswith(".py")
    ]
    if non_py:
        caveats.append(
            f"{len(non_py)} non-Python files — symbol extraction is Python-only"
        )
    if not symbols and any(f.get("path", "").endswith(".py") for f in files):
        caveats.append("no Python symbols extracted from changed .py files")
    if not files:
        caveats.append(
            "no file diff data available — semantic classification is path-based"
        )
    return caveats


def _repo_path(project: str, row: dict[str, Any]) -> str:
    path = row.get("path") or row.get("repo_path") or ""
    if path:
        return str(path)
    return f"/realm/project/{project}"


def _files_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    paths = row.get("paths")
    if isinstance(paths, list):
        return [
            {
                "path": str(p),
                "status": "modified",
                "hunk_count": 0,
                "added_lines": 0,
                "deleted_lines": 0,
            }
            for p in paths[:30]
        ]
    return []


def _counter_dict(value: object) -> Counter[str]:
    if isinstance(value, dict):
        c: Counter[str] = Counter()
        for k, v in value.items():
            try:
                c[str(k)] = int(str(v))
            except ValueError:
                continue
        return c
    return Counter()


def _list(payload: dict[str, Any] | None, key: str) -> list[Any]:
    if payload is None:
        return []
    result = payload.get(key)
    return result if isinstance(result, list) else []


__all__ = [
    "build_active_commit_hunks",
    "build_active_commit_semantics",
    "run_active_commit_hunks",
    "run_active_commit_semantics",
]
