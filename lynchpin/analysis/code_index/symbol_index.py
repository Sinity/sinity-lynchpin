"""Tree-sitter symbol index for active projects.

Produces ``active_symbol_index.json`` — a language-neutral index of
top-level and nested symbols (modules, classes, functions, methods,
structs, enums, traits, impls) extracted from tracked source files in the
active project registry.

This upgrades commit-semantic capsules from string-pattern + Python AST to
real symbol ranges, so a file change can be reported as "modified
``sinex_node_sdk::NodeRuntime::start``" rather than just "lines 42–67 in
``src/lib.rs``". The index is also the substrate for future
``exported_api_changed`` claims.

Languages supported (subject to grammar availability in the devshell):
- Python (via ``tree_sitter_python``)
- Rust (via ``tree_sitter_rust``)

Markdown / Bash / others are gracefully skipped — the artifact records
languages where indexing succeeded and emits a caveat for unindexed ones.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...core.projects import ProjectProfile
from lynchpin.core.io import resolve_analysis_path, save_json
from lynchpin.sources.symbol_extraction import _extract_symbols, _load_parsers
from ..active.git_facts import select_active_profiles, tracked_files

_MAX_FILE_BYTES = 8_000_000

def build_active_symbol_index(
    *,
    projects: Sequence[str] | None = None,
    profiles: Mapping[str, ProjectProfile] | None = None,
    languages: Sequence[str] = ("python", "rust"),
) -> dict[str, Any]:
    selected = select_active_profiles(projects=projects, profiles=profiles)
    parsers = _load_parsers(languages)

    project_rows: list[dict[str, Any]] = []
    caveats: list[str] = []
    for missing in sorted(set(languages) - set(parsers)):
        caveats.append(f"language {missing!r}: tree-sitter grammar unavailable in this environment")

    for name, profile in sorted(selected.items()):
        path = Path(profile.path).expanduser()
        if not path.exists():
            project_rows.append({
                "project": name,
                "exists": False,
                "symbols": [],
                "languages": [],
                "caveats": ["project checkout not present"],
            })
            continue
        omissions: list[dict[str, str]] = []
        rows = list(_index_project(name=name, path=path, parsers=parsers, omissions=omissions))
        languages_seen = sorted({r["language"] for r in rows})
        project_rows.append({
            "project": name,
            "path": str(path),
            "exists": True,
            "symbol_count": len(rows),
            "languages": languages_seen,
            "symbols": rows,
            "omissions": omissions,
            "coverage_complete": not omissions and not caveats,
            "caveats": [f"{len(omissions)} tracked supported source files omitted"] if omissions else [],
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "scope": "tracked files in active project checkouts",
            "extraction": "tree-sitter grammars where available; conservative skip otherwise",
            "qualified_names": "module-relative path with parent symbols joined by '::' (Rust) or '.' (Python)",
            "exported": "Python: not name-mangled (does not start with underscore); "
                        "Rust: declared with 'pub' visibility modifier",
        },
        "languages_indexed": sorted(parsers),
        "projects": project_rows,
        "caveats": caveats,
    }


def run_active_symbol_index(
    out_file: str | PathLike[str],
    *,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_symbol_index(projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


# ── Parser plumbing ──────────────────────────────────────────────────────────


def _index_project(
    *,
    name: str,
    path: Path,
    parsers: dict[str, Any],
    omissions: list[dict[str, str]] | None = None,
) -> Iterable[dict[str, Any]]:
    if not parsers:
        return
    files = tracked_files(path)
    for rel in files:
        lang = _language_for(rel)
        if lang not in parsers:
            continue
        full = path / rel
        try:
            stat = full.stat()
        except OSError as error:
            if omissions is not None:
                omissions.append({"path": rel, "reason": "stat_failed", "error_type": type(error).__name__})
            continue
        if stat.st_size > _MAX_FILE_BYTES:
            if omissions is not None:
                omissions.append({"path": rel, "reason": "size_limit", "bytes": str(stat.st_size), "limit_bytes": str(_MAX_FILE_BYTES)})
            continue
        try:
            source = full.read_bytes()
        except OSError as error:
            if omissions is not None:
                omissions.append({"path": rel, "reason": "read_failed", "error_type": type(error).__name__})
            continue
        if len(source) > _MAX_FILE_BYTES:
            if omissions is not None:
                omissions.append({"path": rel, "reason": "size_limit", "bytes": str(len(source)), "limit_bytes": str(_MAX_FILE_BYTES)})
            continue
        for symbol in _extract_symbols(source=source, parser=parsers[lang], language=lang, project=name, path=rel):
            yield {
                "project": symbol.project,
                "language": symbol.language,
                "path": symbol.path,
                "symbol_kind": symbol.symbol_kind,
                "qualified_name": symbol.qualified_name,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "exported": symbol.exported,
                "parent": symbol.parent,
            }


def _language_for(rel: str) -> str | None:
    if rel.endswith(".py"):
        return "python"
    if rel.endswith(".rs"):
        return "rust"
    return None
