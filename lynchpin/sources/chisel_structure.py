"""Static, source-grounded structure products for Chisel packages.

The builder consumes only a captured inventory. It never discovers files from
the live checkout, invokes package managers, or follows dependency references.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import posixpath
import re
import tomllib
import sys
from importlib.metadata import PackageNotFoundError, version
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .chisel_cache import copy_file

from .chisel_inventory import CapturedInventory

_TOOL_VERSION = "chisel-structure-v4"
_SOURCE_ROLES = {"implementation", "tests", "tooling"}
_SOURCE_EXTENSIONS = {".py": "python", ".rs": "rust"}
_SYMBOL_CACHE_VERSION = _TOOL_VERSION


def build_structure(
    inventory: CapturedInventory,
    package_dir: Path,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Write captured-inventory structure derivatives and return coverage.

    The builder only reads the typed captured inventory. Unknown roles and
    excluded entries remain visible in coverage but never enter source metrics.
    """
    root = Path(inventory.root)
    project = str(inventory.project)
    snapshot_id = str(inventory.snapshot_id)
    policy_version = str(inventory.policy_version)
    out = Path(package_dir) / "structure"
    cache = Path(cache_dir) if cache_dir is not None else None

    records = [
        {
            "path": row.path,
            "sha256": row.sha256,
            "size_bytes": row.size_bytes,
            "role": row.role,
            "included": row.included,
            "included_by": row.included_by,
            "excluded_by": row.excluded_by,
            "role_reason": row.role_reason,
            "source_kind": row.source_kind,
        }
        for row in inventory.files
    ]
    by_path = {str(row["path"]): row for row in records}
    included = [row for row in records if row.get("included")]
    role_counts = Counter(str(row.get("role", "unclassified")) for row in included)
    source_rows = [row for row in included if row.get("role") in _SOURCE_ROLES]
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    parser_missing: set[str] = set()
    parser_versions = {"python": sys.version}
    for distribution in ("tree-sitter", "tree-sitter-rust"):
        try:
            parser_versions[distribution] = version(distribution)
        except PackageNotFoundError:
            parser_versions[distribution] = "unavailable"
    product_key = hashlib.sha256(json.dumps([snapshot_id, policy_version, _TOOL_VERSION, parser_versions], sort_keys=True).encode()).hexdigest()
    product_cache = cache / "products" / product_key if cache else None
    if product_cache is not None and (product_cache / "hashes.json").is_file():
        try:
            hashes = json.loads((product_cache / "hashes.json").read_text())
            if all(hashlib.sha256((product_cache / name).read_bytes()).hexdigest() == expected for name, expected in hashes.items()):
                out.mkdir(parents=True, exist_ok=True)
                for name in hashes:
                    copy_file(product_cache / name, out / name)
                coverage = json.loads((out / "coverage.json").read_text())
                coverage["cache"] = {"hit": True, "key": product_key, "files": len(hashes)}
                (out / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
                return coverage
        except (OSError, ValueError, TypeError):
            pass
    # Capture already verified these bytes. A product cache hit is keyed by the
    # immutable snapshot identity, so rereading every source file here makes
    # unchanged builds pay the full parser-input I/O cost for no new evidence.
    source_bytes: dict[str, bytes] = {}
    source_hash_errors: list[dict[str, str]] = []
    for row in source_rows:
        rel = str(row["path"])
        try:
            data = (root / PurePosixPath(rel)).read_bytes()
        except OSError as exc:
            source_hash_errors.append({"path": rel, "error": str(exc)})
            continue
        actual = hashlib.sha256(data).hexdigest()
        if actual != str(row.get("sha256", "")):
            source_hash_errors.append({"path": rel, "error": "captured hash mismatch"})
            continue
        source_bytes[rel] = data
    if source_hash_errors:
        details = "; ".join(
            f"{row['path']}: {row['error']}" for row in source_hash_errors
        )
        raise ValueError(
            f"captured source inventory failed hash verification: {details}"
        )
    out.mkdir(parents=True, exist_ok=True)
    rust_parser = None
    rust_parser_loaded = False
    python_modules = {
        _python_module(path) for path in source_bytes if path.endswith(".py")
    }
    for row in source_rows:
        path = str(row["path"])
        data = source_bytes.get(path)
        if data is None:
            continue
        role = str(row["role"])
        lang = _SOURCE_EXTENSIONS.get(PurePosixPath(path).suffix)
        text = data.decode("utf-8", errors="replace")
        cache_key = hashlib.sha256(
            json.dumps(
                [
                    _SYMBOL_CACHE_VERSION,
                    policy_version,
                    parser_versions,
                    path,
                    row["sha256"],
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cached = _read_cache(cache, cache_key)
        if cached is not None:
            symbols.extend(
                _bind_cached_row(item, project, snapshot_id, path, row["sha256"])
                for item in cached.get("symbols", [])
            )
            metrics.append(
                _bind_cached_row(
                    cached["metric"], project, snapshot_id, path, row["sha256"]
                )
            )
            if lang == "python":
                try:
                    tree = ast.parse(text, filename=path)
                except SyntaxError:
                    pass
                else:
                    imports.extend(
                        _python_imports(
                            project, snapshot_id, path, row, tree, python_modules
                        )
                    )
            if cached.get("parser_unavailable"):
                parser_missing.add("rust")
            continue
        line_count = len(text.splitlines())
        metric: dict[str, Any] = {
            "project": project,
            "snapshot_id": snapshot_id,
            "path": path,
            "sha256": row["sha256"],
            "role": role,
            "language": lang or "unsupported",
            "bytes": len(data),
            "physical_lines": line_count,
            "symbol_count": 0,
            "function_complexity_sum": None,
            "complexity_method": None,
            "parse_status": "unsupported",
        }
        file_symbols: list[dict[str, Any]] = []
        file_imports: list[dict[str, Any]] = []
        parser_unavailable = False
        if lang == "python":
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError as exc:
                metric["parse_status"] = "parse_error"
                metric["parse_error"] = f"line {exc.lineno}: {exc.msg}"
            else:
                metric["parse_status"] = "parsed"
                metric["function_complexity_sum"] = 0
                metric["complexity_method"] = "decision-count approximation"
                file_symbols, complexities = _python_symbols(
                    tree, project, snapshot_id, path, row
                )
                metric["function_complexity_sum"] = sum(complexities)
                metric["symbol_count"] = len(file_symbols)
                file_imports = _python_imports(
                    project, snapshot_id, path, row, tree, python_modules
                )
        elif lang == "rust":
            # Reuse the repository's tree-sitter extractor where its grammar is
            # available. Missing grammars are reported as a coverage gap.
            from lynchpin.analysis.code_index.symbol_index import (
                _extract_symbols,
                _load_parsers,
            )

            if not rust_parser_loaded:
                rust_parser = _load_parsers(("rust",)).get("rust")
                rust_parser_loaded = True
            parser = rust_parser
            if parser is None:
                parser_missing.add("rust")
                parser_unavailable = True
                metric["parse_status"] = "parser_unavailable"
            else:
                parsed = list(
                    _extract_symbols(
                        source=data,
                        parser=parser,
                        language="rust",
                        project=project,
                        path=path,
                    )
                )
                metric["parse_status"] = "parsed"
                metric["symbol_count"] = len(parsed)
                for item in parsed:
                    file_symbols.append(
                        _symbol(
                            project,
                            snapshot_id,
                            path,
                            row,
                            "rust",
                            item.symbol_kind,
                            item.qualified_name,
                            item.start_line,
                            item.end_line,
                            item.exported,
                            item.parent,
                        )
                    )
        else:
            metric["parse_status"] = "unsupported_language"
        symbols.extend(file_symbols)
        imports.extend(file_imports)
        metrics.append(metric)
        _write_cache(
            cache,
            cache_key,
            {
                "metric": metric,
                "symbols": file_symbols,
                "parser_unavailable": parser_unavailable,
            },
        )

    modules = {
        row["path"]: _python_module(row["path"])
        for row in source_rows
        if row["path"].endswith(".py") and row["path"] in source_bytes
    }
    edges = _resolved_python_edges(imports, modules)
    inventory_rows = _structure_inventory(project, snapshot_id, included, source_rows)
    manifests, manifest_edges, manifest_gaps = _manifests(
        project, snapshot_id, source_bytes, source_rows
    )
    edges.extend(manifest_edges)
    import_edges = [edge for edge in edges if edge.get("kind") == "python_import"]
    declared = {re.split(r"[<>=!~;\[ @]", dep["name"])[0].replace("-", "_")
                for manifest in manifests if manifest.get("ecosystem") == "python" for dep in manifest.get("dependencies", [])}
    for record in imports:
        if record["reference_class"] == "unresolved" and record["requested"].split(".")[0] in declared:
            record["reference_class"] = "declared_third_party"
            record["resolution_caveat"] = "distribution/module spelling match; imports may use another name"
    projections = {
        "all_static_imports": {"predicate": "kind == python_import", "count": len(import_edges)},
        "excluding_type_only": {"predicate": "kind == python_import and not type_only",
                                "count": sum(not edge.get("type_only") for edge in import_edges)},
        "module_initialization": {"predicate": "kind == python_import and not type_only and not deferred",
                                  "count": sum(not edge.get("type_only") and not edge.get("deferred") for edge in import_edges)},
    }
    (out / "graph_projections.json").write_text(json.dumps({
        "schema_version": 1, "snapshot_id": snapshot_id,
        "edge_dataset": "dependency_edges.jsonl", "projections": projections,
        "caveat": "Static import projections preserve conditions; none is an execution graph.",
    }, indent=2, sort_keys=True) + "\n")
    graph_nodes = _graph_node_rows(
        edges, (module for module in modules.values() if module), project, snapshot_id
    )
    cycles = _graph_cycles(graph_nodes, edges, project, snapshot_id)
    config_refs = _static_config_references(
        project, snapshot_id, source_bytes, source_rows, by_path
    )
    for name, rows in (
        ("symbols.jsonl", symbols),
        ("imports.jsonl", imports),
        ("dependency_edges.jsonl", edges),
        ("inventory.jsonl", inventory_rows),
        ("manifests.jsonl", manifests),
        ("config_references.jsonl", config_refs),
        ("graph_nodes.jsonl", graph_nodes),
        ("cycles.jsonl", cycles),
    ):
        _write_jsonl(out / name, rows)
    _write_metrics_csv(out / "file_metrics.csv", metrics)
    _write_graph_csv(out / "graph_metrics.csv", graph_nodes)

    unresolved_edges = [edge for edge in edges if edge.get("status") != "resolved"]
    coverage = {
        "schema_version": 2,
        "cache": {"hit": False, "key": product_key},
        "project": project,
        "snapshot_id": snapshot_id,
        "tool_version": _TOOL_VERSION,
        "policy_version": policy_version,
        "scope": "captured inventory entries marked included with an explicit maintained source role",
        "inventory": {
            "included_files": len(included),
            "included_bytes": sum(int(r.get("size_bytes", 0)) for r in included),
            "role_counts": dict(sorted(role_counts.items())),
            "source_role_files": len(source_rows),
            "source_parsed_files": sum(m["parse_status"] == "parsed" for m in metrics),
            "source_hash_errors": source_hash_errors,
            "excluded_files": len(records) - len(included),
            "unclassified_files": role_counts.get("unclassified", 0),
        },
        "parsers": {
            "python": "stdlib ast",
            "rust": "tree-sitter" if "rust" not in parser_missing else "unavailable",
        },
        "parser_versions": parser_versions,
        "unsupported_or_missing": sorted(parser_missing),
        "unsupported_source_languages": sorted(
            {
                str(metric["language"])
                for metric in metrics
                if metric["parse_status"] == "unsupported_language"
            }
        ),
        "manifest_gaps": manifest_gaps,
        "counts": {
            "symbols": len(symbols),
            "python_import_records": len(imports),
            "dependency_edges": len(edges),
            "unresolved_edges": len(unresolved_edges),
            "config_references": len(config_refs),
        },
        "resolved_graph_nodes": len(graph_nodes),
        "resolved_cycles": len(cycles),
        "limitations": [
            "Python complexity is a decision-count approximation.",
            "Only Python internal imports and statically named manifest dependencies are resolved.",
            "No call graph or architectural/quality conclusions are inferred.",
        ],
    }
    (out / "coverage.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if product_cache is not None:
        product_cache.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for path in out.iterdir():
            if path.is_file():
                copy_file(path, product_cache / path.name)
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (product_cache / "hashes.json").write_text(json.dumps(hashes, sort_keys=True) + "\n")
    return coverage


def _symbol(
    project: str,
    snapshot: str,
    path: str,
    row: Mapping[str, Any],
    language: str,
    kind: str,
    name: str,
    start: int,
    end: int,
    exported: bool,
    parent: str | None = None,
) -> dict[str, Any]:
    return {
        "project": project,
        "snapshot_id": snapshot,
        "path": path,
        "sha256": row["sha256"],
        "language": language,
        "symbol_kind": kind,
        "qualified_name": name,
        "start_line": start,
        "end_line": end,
        "exported": exported,
        "parent": parent,
    }


def _python_complexity(node: ast.AST) -> int:
    """Decision-count approximation for one function, excluding nested scopes."""
    score = 1
    pending = [node]
    while pending:
        current = pending.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            pending.append(child)
            if isinstance(child, ast.BoolOp):
                score += max(0, len(child.values) - 1)
            elif isinstance(child, ast.Try):
                score += (
                    len(child.handlers) + bool(child.orelse) + bool(child.finalbody)
                )
            elif isinstance(child, ast.Match):
                score += len(child.cases)
            elif isinstance(
                child,
                (
                    ast.Assert,
                    ast.AsyncFor,
                    ast.AsyncWith,
                    ast.ExceptHandler,
                    ast.For,
                    ast.If,
                    ast.IfExp,
                    ast.While,
                    ast.With,
                ),
            ):
                score += 1
            elif isinstance(
                child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                score += len(child.generators)
    return score


def _python_symbols(
    tree: ast.AST,
    project: str,
    snapshot: str,
    path: str,
    row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[int]]:
    symbols: list[dict[str, Any]] = []
    complexities: list[int] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        next_parents = parents
        if isinstance(node, ast.ClassDef):
            name = node.name
            symbols.append(
                _symbol(
                    project,
                    snapshot,
                    path,
                    row,
                    "python",
                    "class",
                    ".".join((*parents, name)),
                    node.lineno,
                    node.end_lineno or node.lineno,
                    not name.startswith("_"),
                    ".".join(parents) or None,
                )
            )
            next_parents = (*parents, name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            symbols.append(
                _symbol(
                    project,
                    snapshot,
                    path,
                    row,
                    "python",
                    "async_function"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else "function",
                    ".".join((*parents, name)),
                    node.lineno,
                    node.end_lineno or node.lineno,
                    not name.startswith("_"),
                    ".".join(parents) or None,
                )
            )
            complexities.append(_python_complexity(node))
            next_parents = (*parents, name)
        for child in ast.iter_child_nodes(node):
            visit(child, next_parents)

    visit(tree, ())
    return symbols, complexities


def _python_module(path: str) -> str:
    p = PurePosixPath(path).with_suffix("")
    parts = list(p.parts)
    # Standard src-layout packages expose src/pkg as import pkg.
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_imports(
    project: str,
    snapshot: str,
    path: str,
    row: Mapping[str, Any],
    tree: ast.AST,
    known_modules: set[str],
) -> list[dict[str, Any]]:
    module = _python_module(path)
    package = (
        module
        if PurePosixPath(path).name == "__init__.py"
        else module.rpartition(".")[0]
    )
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    exported = set()
    for statement in getattr(tree, "body", []):
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in statement.targets):
            try:
                exported.update(ast.literal_eval(statement.value))
            except (ValueError, TypeError):
                pass
    found: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        names: list[tuple[str, int]] = []
        dynamic = False
        if isinstance(node, ast.Import):
            names = [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                base_parts = base_parts[: max(0, len(base_parts) - node.level + 1)]
                base_parts.extend(node.module.split(".") if node.module else [])
                base = ".".join(base_parts)
            else:
                # Absolute imports start at the declared module, never at the
                # importing file's package.
                base = node.module or ""
            names = (
                [(base, node.level)]
                if not node.names
                else [
                    (f"{base}.{alias.name}" if base else alias.name, node.level)
                    for alias in node.names
                    if alias.name != "*"
                ]
                or [(base, node.level)]
            )
        elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib" and node.func.attr == "import_module"):
            dynamic = True
            names = [(str(node.args[0].value), 0) if node.args and isinstance(node.args[0], ast.Constant)
                     and isinstance(node.args[0].value, str) else (ast.unparse(node), 0)]
        if not names:
            continue
        ancestors = []
        parent = parents.get(node)
        while parent is not None:
            ancestors.append(parent)
            parent = parents.get(parent)
        functions = [p for p in ancestors if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        symbols = [p.name for p in reversed(ancestors) if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        conditions = [(ast.unparse(p.test) if any(node is d for body in p.body for d in ast.walk(body))
                       else f"not ({ast.unparse(p.test)})") for p in ancestors if isinstance(p, ast.If)]
        type_only = any(isinstance(p, ast.If) and isinstance(p.test, (ast.Name, ast.Attribute))
                        and (getattr(p.test, "id", None) == "TYPE_CHECKING" or getattr(p.test, "attr", None) == "TYPE_CHECKING")
                        and any(node is descendant for body in p.body for descendant in ast.walk(body)) for p in ancestors)
        optional = any(isinstance(p, (ast.Try, ast.TryStar)) and any(
            h.type is None or any(isinstance(n, ast.Name) and n.id in {"ImportError", "ModuleNotFoundError"} for n in ast.walk(h.type))
            for h in p.handlers) for p in ancestors)
        for index, (requested, level) in enumerate(names):
            target = requested
            parts = target.split(".")
            while parts and ".".join(parts) not in known_modules:
                parts.pop()
            resolved = ".".join(parts) if parts else None
            found.append(
                {
                    "project": project,
                    "snapshot_id": snapshot,
                    "path": path,
                    "sha256": row["sha256"],
                    "line": node.lineno,
                    "source_module": module,
                    "requested": requested,
                    "relative_level": level,
                    "scope": "function" if functions else "class" if symbols else "module",
                    "enclosing_symbol": ".".join(symbols) or None,
                    "type_only": type_only,
                    "deferred": bool(functions),
                    "conditions": conditions,
                    "optional": optional,
                    "alias": node.names[index].asname if not dynamic and index < len(node.names) else None,
                    "explicit_reexport": (node.names[index].asname == node.names[index].name or node.names[index].name in exported) if not dynamic and index < len(node.names) else False,
                    "reference_class": "dynamic" if dynamic else "internal" if resolved else "standard_library" if requested.split(".")[0] in sys.stdlib_module_names else "unresolved",
                    "method": "python_ast_dynamic_import_candidate" if dynamic else "python_ast_static_import",

                    "target_module": resolved,
                    "status": "candidate_internal"
                    if resolved
                    else "external_or_unresolved",
                }
            )
    return found


def _resolved_python_edges(
    imports: Iterable[Mapping[str, Any]], modules: Mapping[str, str]
) -> list[dict[str, Any]]:
    output = []
    for row in imports:
        source = modules.get(str(row["path"]))
        target = row.get("target_module")
        if source and target:
            output.append(
                {
                    "project": row["project"],
                    "snapshot_id": row["snapshot_id"],
                    "from": source,
                    "to": target,
                    "kind": "python_dynamic_import" if row.get("reference_class") == "dynamic" else "python_import",
                    "type_only": row.get("type_only", False),
                    "deferred": row.get("deferred", False),
                    "conditions": row.get("conditions", []),
                    "optional": row.get("optional", False),
                    "method": row.get("method"),
                    "status": "resolved",
                    "source_path": row["path"],
                    "source_line": row["line"],
                    "source_sha256": row["sha256"],
                }
            )
        elif source and not target:
            output.append(
                {
                    "project": row["project"],
                    "snapshot_id": row["snapshot_id"],
                    "from": source,
                    "to": row["requested"],
                    "kind": "python_dynamic_import" if row.get("reference_class") == "dynamic" else "python_import",
                    "type_only": row.get("type_only", False),
                    "deferred": row.get("deferred", False),
                    "conditions": row.get("conditions", []),
                    "optional": row.get("optional", False),
                    "method": row.get("method"),
                    "status": "unresolved",
                    "source_path": row["path"],
                    "source_line": row["line"],
                    "source_sha256": row["sha256"],
                }
            )
    return output


def _graph_node_rows(
    edges: list[dict[str, Any]],
    all_nodes: Iterable[str],
    project: str,
    snapshot: str,
) -> list[dict[str, Any]]:
    resolved = [edge for edge in edges if edge.get("status") == "resolved"]
    incoming_neighbors: dict[str, set[str]] = defaultdict(set)
    outgoing_neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in resolved:
        incoming_neighbors[str(edge["to"])].add(str(edge["from"]))
        outgoing_neighbors[str(edge["from"])].add(str(edge["to"]))
    incoming = Counter(str(edge["to"]) for edge in resolved)
    outgoing = Counter(str(edge["from"]) for edge in resolved)
    nodes = sorted(set(all_nodes) | set(incoming) | set(outgoing))
    components = _strong_components(nodes, resolved)
    cyclic_components = [
        component
        for component in components
        if len(component) > 1
        or any(edge["from"] == edge["to"] == component[0] for edge in resolved)
    ]
    component_for = {
        node: f"scc-{index:04d}"
        for index, component in enumerate(sorted(cyclic_components), 1)
        for node in component
    }
    return [
        {
            "project": project,
            "snapshot_id": snapshot,
            "node": node,
            "in_degree": incoming[node],
            "incoming_occurrences": incoming[node],
            "outgoing_occurrences": outgoing[node],
            "distinct_in_neighbors": len(incoming_neighbors[node]),
            "distinct_out_neighbors": len(outgoing_neighbors[node]),
            "out_degree": outgoing[node],
            "cycle_component": component_for.get(node),
            "in_cycle": node in component_for,
        }
        for node in nodes
    ]


def _strong_components(
    nodes: list[str], edges: list[dict[str, Any]]
) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in edges:
        if edge.get("status") == "resolved":
            adjacency[str(edge["from"])].add(str(edge["to"]))
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(adjacency[node]):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] == indexes[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(nodes):
        if node not in indexes:
            visit(node)
    return sorted(components)


def _graph_cycles(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    project: str,
    snapshot: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in nodes:
        group = row.get("cycle_component")
        if group:
            groups[str(group)].append(str(row["node"]))
    resolved = [edge for edge in edges if edge.get("status") == "resolved"]
    return [
        {
            "project": project,
            "snapshot_id": snapshot,
            "component_id": component,
            "nodes": sorted(members),
            "node_count": len(members),
            "internal_edge_count": sum(
                edge["from"] in members and edge["to"] in members for edge in resolved
            ),
        }
        for component, members in sorted(groups.items())
    ]


def _structure_inventory(
    project: str,
    snapshot: str,
    rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        path = str(row["path"])
        lower = path.lower()
        categories = []
        if (
            "/tests/" in f"/{lower}"
            or lower.startswith("tests/")
            or lower.endswith(("_test.py", ".spec.ts", ".test.ts"))
        ):
            categories.append("test_source")
        if lower.startswith(".github/workflows/") or "/.github/workflows/" in lower:
            categories.append("workflow")
        if PurePosixPath(path).name in {
            "pyproject.toml",
            "Cargo.toml",
            "package.json",
            "flake.nix",
            "Makefile",
            "justfile",
        }:
            categories.append("build_or_package_manifest")
        if any(
            part in {"config", "configs", "schema", "schemas"}
            for part in PurePosixPath(path).parts
        ):
            categories.append("config_or_schema")
        if row.get("role") not in _SOURCE_ROLES:
            categories.append("project_context_or_evidence")
        output.append(
            {
                "project": project,
                "snapshot_id": snapshot,
                "path": path,
                "sha256": row.get("sha256"),
                "role": row.get("role", "unclassified"),
                "size_bytes": row.get("size_bytes"),
                "categories": categories,
                "included": True,
            }
        )
    return output


def _manifests(
    project: str,
    snapshot: str,
    sources: Mapping[str, bytes],
    source_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    gaps: list[str] = []
    for path, data in sources.items():
        name = PurePosixPath(path).name
        if name not in {"Cargo.toml", "pyproject.toml", "package.json"}:
            continue
        try:
            if name == "Cargo.toml":
                parsed = tomllib.loads(data.decode("utf-8"))
                package = (
                    parsed.get("package", {}).get("name")
                    or _cargo_workspace_name(parsed)
                    or PurePosixPath(path).parent.name
                )
                dependencies: list[dict[str, Any]] = []
                for group in ("dependencies", "dev-dependencies", "build-dependencies"):
                    dependencies.extend(
                        {"name": dep, "declaration": declaration, "group": group}
                        for dep, declaration in _mapping(parsed.get(group)).items()
                    )
                for target, config in _mapping(parsed.get("target")).items():
                    for group in (
                        "dependencies",
                        "dev-dependencies",
                        "build-dependencies",
                    ):
                        dependencies.extend(
                            {
                                "name": dep,
                                "declaration": declaration,
                                "group": f"target.{target}.{group}",
                            }
                            for dep, declaration in _mapping(config.get(group)).items()
                        )
                workspace = _mapping(parsed.get("workspace"))
                dependencies.extend(
                    {
                        "name": dep,
                        "declaration": declaration,
                        "group": "workspace.dependencies",
                    }
                    for dep, declaration in _mapping(
                        workspace.get("dependencies")
                    ).items()
                )
                entrypoints = [
                    str(x)
                    for x in parsed.get("lib", {}).get("path", "src/lib.rs")
                    and [parsed.get("lib", {}).get("path", "src/lib.rs")]
                ]
                entrypoints += [
                    str(target.get("path") or f"src/bin/{target.get('name','')}.rs")
                    for target in parsed.get("bin", [])
                    if isinstance(target, dict)
                ]
                member = {
                    "ecosystem": "cargo",
                    "package": package,
                    "dependencies": dependencies,
                    "entrypoints": entrypoints,
                    "workspace_members": workspace.get("members", []),
                    "workspace_default_members": workspace.get("default-members", []),
                }
            elif name == "pyproject.toml":
                parsed = tomllib.loads(data.decode("utf-8"))
                proj = parsed.get("project", {})
                deps = list(proj.get("dependencies", []))
                optional = _mapping(proj.get("optional-dependencies"))
                for group, values in optional.items():
                    deps.extend(values)
                scripts = _mapping(proj.get("scripts"))
                entrypoints = [f"{key}={value}" for key, value in scripts.items()]
                member = {
                    "ecosystem": "python",
                    "package": proj.get("name") or PurePosixPath(path).parent.name,
                    "dependencies": [
                        {
                            "name": str(dep),
                            "declaration": str(dep),
                            "group": "project.dependencies",
                        }
                        for dep in deps
                    ],
                    "entrypoints": entrypoints,
                    "workspace_members": _mapping(parsed.get("tool"))
                    .get("uv", {})
                    .get("workspace", {})
                    .get("members", []),
                }
            else:
                parsed = json.loads(data)
                deps = []
                for group in (
                    "dependencies",
                    "devDependencies",
                    "peerDependencies",
                    "optionalDependencies",
                ):
                    deps.extend(
                        {"name": str(dep), "declaration": declaration, "group": group}
                        for dep, declaration in _mapping(parsed.get(group)).items()
                    )
                scripts = _mapping(parsed.get("scripts"))
                member = {
                    "ecosystem": "npm",
                    "package": parsed.get("name") or PurePosixPath(path).parent.name,
                    "dependencies": deps,
                    "entrypoints": [f"{key}={value}" for key, value in scripts.items()],
                    "workspace_members": parsed.get("workspaces", []),
                }
            row = {
                "project": project,
                "snapshot_id": snapshot,
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "status": "parsed",
                **member,
            }
            rows.append(row)
            manifest_module = f"manifest:{path}"
            for dependency in member["dependencies"]:
                declaration = dependency["declaration"]
                dep = dependency["name"]
                group = dependency["group"]
                edges.append(
                    {
                        "project": project,
                        "snapshot_id": snapshot,
                        "from": manifest_module,
                        "to": str(dep),
                        "kind": f"{member['ecosystem']}:{group}",
                        "status": "declared_external_or_workspace_dependency",
                        "source_path": path,
                        "source_line": _manifest_line(data, str(dep), declaration),
                        "source_sha256": row["sha256"],
                        "dependency_name": str(dep),
                        "declaration": declaration,
                    }
                )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            tomllib.TOMLDecodeError,
            TypeError,
            AttributeError,
        ) as exc:
            rows.append(
                {
                    "project": project,
                    "snapshot_id": snapshot,
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "status": "parse_error",
                    "error": str(exc),
                }
            )
            gaps.append(f"manifest_parse_error:{path}")
    return rows, edges, gaps


def _cargo_workspace_name(parsed: Mapping[str, Any]) -> str | None:
    return None


def _manifest_line(data: bytes, name: str, declaration: Any) -> int | None:
    """Find a direct manifest key location without claiming parser line data."""
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    quoted = re.escape(name)
    patterns = (
        re.compile(rf"^\s*{quoted}\s*="),
        re.compile(rf"^\s*['\"]{quoted}['\"]\s*:"),
    )
    for number, line in enumerate(lines, 1):
        if any(pattern.search(line) for pattern in patterns):
            return number
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


_PATH_REFERENCE = re.compile(r"(?<![\w])(?:\.{1,2}/[\w./-]+|\./[\w./-]+)")
_GITHUB_REPOSITORY_URL = re.compile(
    r"(?:git\+)?(?:(?:https?|ssh)://(?:git@)?github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\.git)?"
)


def _static_config_references(
    project: str,
    snapshot: str,
    sources: Mapping[str, bytes],
    rows: list[dict[str, Any]],
    inventory_by_path: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    row_by_path = {str(row["path"]): row for row in rows}
    for path, data in sources.items():
        suffix = PurePosixPath(path).suffix
        if suffix not in {
            ".nix",
            ".yaml",
            ".yml",
            ".toml",
            ".json",
            ".conf",
            ".cfg",
        } and PurePosixPath(path).name not in {"Makefile", "justfile"}:
            continue
        text = data.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in _GITHUB_REPOSITORY_URL.finditer(line):
                result.append(
                    {
                        "project": project,
                        "snapshot_id": snapshot,
                        "path": path,
                        "sha256": row_by_path[path]["sha256"],
                        "line": line_no,
                        "reference_type": "repository_url",
                        "repository_url": match.group(0),
                        "repository_slug": match.group(1).removesuffix(".git"),
                        "status": "explicit_repository_url",
                    }
                )
            for match in _PATH_REFERENCE.finditer(line):
                ref = match.group(0)
                resolved = str((PurePosixPath(path).parent / ref))
                normalized = posixpath.normpath(resolved)
                target = inventory_by_path.get(normalized)
                result.append(
                    {
                        "project": project,
                        "snapshot_id": snapshot,
                        "path": path,
                        "sha256": row_by_path[path]["sha256"],
                        "line": line_no,
                        "reference_type": "local_path",
                        "reference": ref,
                        "resolved_inventory_path": normalized if target else None,
                        "status": "static_path_candidate_present"
                        if target
                        else "static_path_candidate_missing",
                    }
                )
    return result


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _read_cache(cache: Path | None, key: str) -> dict[str, Any] | None:
    if cache is None:
        return None
    path = cache / f"{key}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bind_cached_row(
    value: Mapping[str, Any], project: str, snapshot: str, path: str, digest: str
) -> dict[str, Any]:
    """Attach current snapshot provenance to project independent extraction."""
    return {
        **value,
        "project": project,
        "snapshot_id": snapshot,
        "path": path,
        "sha256": digest,
    }


def _write_cache(cache: Path | None, key: str, value: Mapping[str, Any]) -> None:
    if cache is None:
        return
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{key}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(target)


def _write_metrics_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "project",
        "snapshot_id",
        "path",
        "sha256",
        "role",
        "language",
        "bytes",
        "physical_lines",
        "symbol_count",
        "function_complexity_sum",
        "complexity_method",
        "parse_status",
        "parse_error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_graph_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "project",
        "snapshot_id",
        "node",
        "in_degree",
        "out_degree",
        "incoming_occurrences", "outgoing_occurrences",
        "distinct_in_neighbors", "distinct_out_neighbors",
        "cycle_component",
        "in_cycle",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_portfolio_links(root: Path, plans: Iterable[Any]) -> dict[str, Any]:
    """Resolve only explicit manifest paths and repository URLs across projects."""
    root = Path(root)
    selected = list(plans)
    slugs: dict[str, list[str]] = defaultdict(list)
    roots: dict[str, str] = {}
    for plan in selected:
        roots[str(plan.name)] = os.path.abspath(os.path.normpath(os.fspath(plan.path)))
        slug = getattr(plan, "github_slug", None)
        if slug:
            slugs[str(slug).strip("/").casefold()].append(str(plan.name))

    links: list[dict[str, Any]] = []
    skipped = Counter()
    for plan in selected:
        project = str(plan.name)
        structure = root / project / "structure"
        edges_path = structure / "dependency_edges.jsonl"
        if edges_path.is_file():
            for edge in _read_jsonl(edges_path):
                declaration = edge.get("declaration")
                target_name: str | None = None
                relation: str | None = None
                unresolved_reason: str | None = None
                candidate_path: str | None = None
                if isinstance(declaration, Mapping):
                    raw_path = declaration.get("path")
                    if isinstance(raw_path, str) and raw_path:
                        manifest_parent = PurePosixPath(str(edge["source_path"])).parent
                        candidate_path = os.path.abspath(
                            os.path.normpath(
                                os.path.join(
                                    os.fspath(plan.path),
                                    os.fspath(manifest_parent),
                                    raw_path,
                                )
                            )
                        )
                        matches = _projects_containing(candidate_path, roots)
                        if len(matches) == 1:
                            if matches[0] == project:
                                skipped["same_project_reference"] += 1
                            else:
                                target_name, relation = (
                                    matches[0],
                                    "manifest_local_path",
                                )
                        elif len(matches) > 1:
                            unresolved_reason = "ambiguous_configured_path"
                        else:
                            unresolved_reason = "path_outside_selected_projects"
                    raw_git = declaration.get("git")
                    if target_name is None and isinstance(raw_git, str):
                        slug = _github_slug(raw_git)
                        if slug:
                            matches = slugs.get(slug.casefold(), [])
                            if len(matches) == 1:
                                target_name, relation = (
                                    matches[0],
                                    "manifest_repository_url",
                                )
                            elif len(matches) > 1:
                                unresolved_reason = (
                                    "ambiguous_configured_repository_url"
                                )
                            else:
                                unresolved_reason = "repository_not_selected"
                elif isinstance(declaration, str) and declaration.startswith("file:"):
                    manifest_parent = PurePosixPath(str(edge["source_path"])).parent
                    candidate_path = os.path.abspath(
                        os.path.normpath(
                            os.path.join(
                                os.fspath(plan.path),
                                os.fspath(manifest_parent),
                                declaration.removeprefix("file:"),
                            )
                        )
                    )
                    matches = _projects_containing(candidate_path, roots)
                    if len(matches) == 1:
                        if matches[0] == project:
                            skipped["same_project_reference"] += 1
                        else:
                            target_name, relation = matches[0], "manifest_local_path"
                    elif len(matches) > 1:
                        unresolved_reason = "ambiguous_configured_path"
                    else:
                        unresolved_reason = "path_outside_selected_projects"
                elif isinstance(declaration, str):
                    slug = _github_slug(declaration)
                    if slug:
                        matches = slugs.get(slug.casefold(), [])
                        if len(matches) == 1:
                            target_name, relation = (
                                matches[0],
                                "manifest_repository_url",
                            )
                        elif len(matches) > 1:
                            unresolved_reason = "ambiguous_configured_repository_url"
                        else:
                            unresolved_reason = "repository_not_selected"
                if target_name:
                    if target_name == project:
                        skipped["self_reference"] += 1
                        continue
                    target_capture = _read_json(root / target_name / "capture.json")
                    target_snapshot = target_capture.get("snapshot_id")
                    if not isinstance(target_snapshot, str) or not target_snapshot:
                        skipped["missing_target_snapshot_id"] += 1
                        continue
                    links.append(
                        {
                            "source_project": project,
                            "source_snapshot_id": edge.get("snapshot_id"),
                            "source_path": edge.get("source_path"),
                            "source_sha256": edge.get("source_sha256"),
                            "source_line": edge.get("source_line"),
                            "dependency_name": edge.get("dependency_name"),
                            "declaration": declaration,
                            "relation": relation,
                            "target_project": target_name,
                            "target_snapshot_id": target_snapshot,
                            "matched_path": candidate_path,
                            "status": "resolved_explicit_reference",
                        }
                    )
                elif unresolved_reason:
                    skipped[unresolved_reason] += 1

        refs_path = structure / "config_references.jsonl"
        if refs_path.is_file():
            for ref in _read_jsonl(refs_path):
                slug = ref.get("repository_slug")
                if not isinstance(slug, str):
                    continue
                matches = slugs.get(slug.casefold(), [])
                if len(matches) != 1:
                    skipped[
                        "ambiguous_configured_repository_url"
                        if matches
                        else "repository_not_selected"
                    ] += 1
                    continue
                target_name = matches[0]
                if target_name == project:
                    skipped["self_reference"] += 1
                    continue
                target_capture = _read_json(root / target_name / "capture.json")
                target_snapshot = target_capture.get("snapshot_id")
                if not isinstance(target_snapshot, str) or not target_snapshot:
                    skipped["missing_target_snapshot_id"] += 1
                    continue
                links.append(
                    {
                        "source_project": project,
                        "source_snapshot_id": ref.get("snapshot_id"),
                        "source_path": ref.get("path"),
                        "source_sha256": ref.get("sha256"),
                        "source_line": ref.get("line"),
                        "declaration": ref.get("repository_url"),
                        "relation": "static_repository_url",
                        "target_project": target_name,
                        "target_snapshot_id": target_snapshot,
                        "matched_path": None,
                        "status": "resolved_explicit_reference",
                    }
                )

    links.sort(
        key=lambda row: (
            str(row["source_project"]),
            str(row["source_path"]),
            int(row["source_line"] or 0),
            str(row["target_project"]),
        )
    )
    _write_jsonl(root / "cross-project-links.jsonl", links)
    coverage = {
        "schema_version": 1,
        "project_count": len(selected),
        "resolved_links": len(links),
        "unresolved_or_ambiguous_references": sum(skipped.values()),
        "skipped_reasons": dict(sorted(skipped.items())),
        "matching": "exact configured repository path containment or exact GitHub owner/repository URL; names alone never match",
    }
    (root / "cross-project-links.coverage.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return coverage


def _projects_containing(candidate_path: str, roots: Mapping[str, str]) -> list[str]:
    matches = []
    for name, project_root in roots.items():
        try:
            if os.path.commonpath((candidate_path, project_root)) == project_root:
                matches.append(name)
        except ValueError:
            continue
    if not matches:
        return []
    max_root_length = max(len(roots[name]) for name in matches)
    return sorted(name for name in matches if len(roots[name]) == max_root_length)


def _github_slug(value: str) -> str | None:
    match = _GITHUB_REPOSITORY_URL.search(value.strip())
    if not match:
        return None
    owner, repository = match.group(1).split("/", 1)
    return f"{owner}/{repository.removesuffix('.git')}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
