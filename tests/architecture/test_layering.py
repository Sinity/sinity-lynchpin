import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_LYNCHPIN_ROOT = _REPO_ROOT / "lynchpin"
_PACKAGES = {"core", "sources", "substrate", "graph", "analysis", "cli", "mcp", "ingest"}
_FORBIDDEN_UPWARD_IMPORTS = {
    "core": {"sources", "substrate", "graph", "analysis", "cli", "mcp", "ingest"},
    "sources": {"substrate", "graph", "analysis", "cli", "mcp"},
    "substrate": {"graph", "analysis", "cli", "mcp"},
}


def test_lower_layers_do_not_import_higher_layers():
    violations: list[str] = []

    for path in _LYNCHPIN_ROOT.rglob("*.py"):
        source_layer = path.relative_to(_LYNCHPIN_ROOT).parts[0]
        if source_layer not in _PACKAGES:
            continue
        forbidden = _FORBIDDEN_UPWARD_IMPORTS.get(source_layer, set())
        if not forbidden:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"))
        for imported in _lynchpin_imports(path, tree):
            parts = imported.split(".")
            if len(parts) < 2:
                continue
            target_layer = parts[1]
            if target_layer in forbidden:
                rel = path.relative_to(_REPO_ROOT)
                violations.append(f"{rel}: {source_layer} imports {target_layer} via {imported}")

    assert violations == []


def _lynchpin_imports(path: Path, tree: ast.AST) -> list[str]:
    imports: list[str] = []
    rel_parts = path.relative_to(_LYNCHPIN_ROOT).parts

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name.startswith("lynchpin."))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            resolved = _resolve_relative_import(rel_parts, level=node.level, module=node.module)
            if resolved is not None:
                imports.append(resolved)
        elif node.module and node.module.startswith("lynchpin."):
            imports.append(node.module)
    return imports


def _resolve_relative_import(
    rel_parts: tuple[str, ...],
    *,
    level: int,
    module: str | None,
) -> str | None:
    base = list(rel_parts[:-1])
    parent_hops = level - 1
    if parent_hops:
        base = base[:-parent_hops]
    if module:
        base.extend(module.split("."))
    if not base:
        return None
    return "lynchpin." + ".".join(base)
