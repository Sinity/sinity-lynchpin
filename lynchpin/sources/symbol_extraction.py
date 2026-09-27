"""Source-local Tree-sitter symbol parsing shared by evidence readers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


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
