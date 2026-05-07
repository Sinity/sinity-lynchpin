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
from dataclasses import dataclass
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...core.projects import ProjectProfile
from ..core.io import resolve_analysis_path, save_json
from ..active.git_facts import select_active_profiles, tracked_files

_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class SymbolRow:
    project: str
    language: str
    path: str
    symbol_kind: str
    qualified_name: str
    start_line: int
    end_line: int
    exported: bool
    parent: str | None


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
        rows = list(_index_project(name=name, path=path, parsers=parsers))
        languages_seen = sorted({r["language"] for r in rows})
        project_rows.append({
            "project": name,
            "path": str(path),
            "exists": True,
            "symbol_count": len(rows),
            "languages": languages_seen,
            "symbols": rows,
            "caveats": [],
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


def _load_parsers(languages: Sequence[str]) -> dict[str, Any]:
    """Lazy-load tree-sitter parsers for requested languages."""
    out: dict[str, Any] = {}
    try:
        import tree_sitter  # type: ignore[import-not-found]
    except ImportError:
        return out

    if "python" in languages:
        try:
            import tree_sitter_python  # type: ignore[import-not-found]
            parser = tree_sitter.Parser()
            parser.language = tree_sitter.Language(tree_sitter_python.language())
            out["python"] = parser
        except (ImportError, AttributeError):
            pass

    if "rust" in languages:
        try:
            import tree_sitter_rust  # type: ignore[import-not-found]
            parser = tree_sitter.Parser()
            parser.language = tree_sitter.Language(tree_sitter_rust.language())
            out["rust"] = parser
        except (ImportError, AttributeError):
            pass

    return out


def _index_project(
    *,
    name: str,
    path: Path,
    parsers: dict[str, Any],
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
        except OSError:
            continue
        if stat.st_size > _MAX_FILE_BYTES:
            continue
        try:
            source = full.read_bytes()
        except OSError:
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


# ── Symbol extraction (language-specific) ────────────────────────────────────


def _extract_symbols(
    *,
    source: bytes,
    parser: Any,
    language: str,
    project: str,
    path: str,
) -> Iterable[SymbolRow]:
    try:
        tree = parser.parse(source)
    except Exception:
        return
    if language == "python":
        yield from _walk_python(tree.root_node, project=project, path=path, parents=())
    elif language == "rust":
        yield from _walk_rust(tree.root_node, project=project, path=path, parents=())


def _walk_python(node: Any, *, project: str, path: str, parents: tuple[str, ...]) -> Iterable[SymbolRow]:
    name_kind = _python_node_kind(node.type)
    next_parents = parents
    if name_kind:
        ident = _python_identifier(node)
        if ident:
            qualified = ".".join((*parents, ident))
            exported = not ident.startswith("_")
            yield SymbolRow(
                project=project,
                language="python",
                path=path,
                symbol_kind=name_kind,
                qualified_name=qualified,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                exported=exported,
                parent=parents[-1] if parents else None,
            )
            next_parents = (*parents, ident)
    for child in node.children:
        yield from _walk_python(child, project=project, path=path, parents=next_parents)


def _python_node_kind(t: str) -> str | None:
    return {
        "function_definition": "function",
        "class_definition": "class",
        "decorated_definition": None,  # children carry the actual definition
    }.get(t)


def _python_identifier(node: Any) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
    return None


def _walk_rust(node: Any, *, project: str, path: str, parents: tuple[str, ...]) -> Iterable[SymbolRow]:
    kind = _rust_node_kind(node.type)
    next_parents = parents
    if kind:
        ident = _rust_identifier(node)
        if ident:
            qualified = "::".join((*parents, ident))
            exported = _rust_is_pub(node)
            yield SymbolRow(
                project=project,
                language="rust",
                path=path,
                symbol_kind=kind,
                qualified_name=qualified,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                exported=exported,
                parent=parents[-1] if parents else None,
            )
            # Descend into mod/impl bodies under the new parent name; for fn/struct/etc
            # nested symbols (closures, etc.) are skipped because they are rarely useful
            # at the symbol-index granularity.
            if node.type in {"mod_item", "impl_item", "trait_item"}:
                next_parents = (*parents, ident)
    for child in node.children:
        yield from _walk_rust(child, project=project, path=path, parents=next_parents)


def _rust_node_kind(t: str) -> str | None:
    return {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
        "type_item": "type_alias",
    }.get(t)


def _rust_identifier(node: Any) -> str | None:
    # impl_item: identifier comes from the `type_identifier` child or via the type path
    # function_item / struct_item / etc.: 'identifier' child or 'type_identifier'
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return None


def _rust_is_pub(node: Any) -> bool:
    for child in node.children:
        if child.type == "visibility_modifier":
            text = child.text.decode("utf-8", errors="replace")
            if text.startswith("pub"):
                return True
    return False
