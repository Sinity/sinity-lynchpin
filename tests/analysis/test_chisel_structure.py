"""Source-structure products use only explicitly maintained captured files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from lynchpin.sources.chisel_inventory import CapturedInventory, InventoryFile
from lynchpin.sources.chisel_structure import build_structure


def _inventory(
    tmp_path: Path,
    entries: dict[str, tuple[str, str]],
    *,
    project: str = "fixture",
    snapshot: str = "snapshot-1",
) -> CapturedInventory:
    root = tmp_path / "captured"
    root.mkdir(parents=True)
    files = []
    for path, (text, role) in entries.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode()
        target.write_bytes(data)
        files.append(
            InventoryFile(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                role=role,
                role_reason="synthetic fixture classification",
                included_by=("slice:test",),
                excluded_by=(),
                source_kind="file",
                included=True,
            )
        )
    return CapturedInventory(
        root=root,
        files=tuple(files),
        memberships={"test": tuple(entries)},
        project=project,
        generated_at="2026-01-01T00:00:00+00:00",
        revision="abc123",
        dirty=False,
        policy_version="chisel-role-policy-1",
        snapshot_id=snapshot,
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_metrics_symbols_and_imports_use_maintained_captured_source(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        {
            "src/pkg.py": (
                "from util import helper\n\nclass Public:\n    def run(self):\n        if True:\n            def inner():\n                if False: return 2\n            return 1\nclass Other:\n    def run(self):\n        return 3\n",
                "implementation",
            ),
            "src/util.py": (
                "def helper():\n    return 2\n",
                "implementation",
            ),
            "tests/test_pkg.py": (
                "def test_run():\n    assert True\n",
                "tests",
            ),
            ".agent/scratch/note.py": (
                "def fake():\n    if True: pass\n",
                "context",
            ),
            "docs/plan.md": (
                "```python\ndef also_fake(): pass\n```\n",
                "documentation",
            ),
        },
    )
    output = tmp_path / "package"
    coverage = build_structure(inventory, output)
    metrics = list(
        __import__("csv").DictReader((output / "structure/file_metrics.csv").open())
    )
    assert {row["path"] for row in metrics} == {
        "src/pkg.py",
        "src/util.py",
        "tests/test_pkg.py",
    }
    assert sum(int(row["physical_lines"]) for row in metrics) == 15
    symbols = _jsonl(output / "structure/symbols.jsonl")
    assert {row["qualified_name"] for row in symbols} == {
        "Public",
        "Public.run",
        "Public.run.inner",
        "Other",
        "Other.run",
        "helper",
        "test_run",
    }
    runs = [row for row in symbols if row["qualified_name"].endswith(".run")]
    assert len(runs) == 2
    assert all(row["end_line"] >= row["start_line"] for row in runs)
    assert {row["parent"] for row in runs} == {"Public", "Other"}
    assert all(row["sha256"] for row in symbols)
    imports = _jsonl(output / "structure/imports.jsonl")
    assert imports[0]["target_module"] == "util"
    edges = _jsonl(output / "structure/dependency_edges.jsonl")
    assert any(row["status"] == "resolved" and row["to"] == "util" for row in edges)
    assert coverage["inventory"]["unclassified_files"] == 0
    assert all(
        "fake" not in str(row) and "also_fake" not in str(row) for row in symbols
    )
    # The outer run complexity excludes its nested function's `if` branch.
    public_run_metric = next(row for row in metrics if row["path"] == "src/pkg.py")
    assert int(public_run_metric["function_complexity_sum"]) == 5


def test_rust_symbols_and_static_manifest_edges_are_inventory_bound(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inventory = _inventory(
        tmp_path,
        {
            "Cargo.toml": (
                "[package]\nname='sample'\nversion='0.1.0'\n[dependencies]\nserde='1'\n",
                "tooling",
            ),
            "src/lib.rs": (
                "pub struct Thing;\nimpl Thing { pub fn new() -> Self { Self } }\n",
                "implementation",
            ),
            "README.md": ("Do not count this as source.\n", "documentation"),
        },
    )
    output = tmp_path / "package"
    coverage = build_structure(inventory, output, cache_dir=tmp_path / "cache")
    symbols = _jsonl(output / "structure/symbols.jsonl")
    assert {row["qualified_name"] for row in symbols} >= {"Thing"}
    manifests = _jsonl(output / "structure/manifests.jsonl")
    assert manifests[0]["package"] == "sample"
    edges = _jsonl(output / "structure/dependency_edges.jsonl")
    assert any(
        row["to"] == "serde"
        and row["status"] == "declared_external_or_workspace_dependency"
        for row in edges
    )
    assert coverage["inventory"]["source_role_files"] == 2
    # A verified snapshot cache hit must avoid reopening the captured tree.
    original_read_bytes = Path.read_bytes

    def cache_only_read(path: Path) -> bytes:
        if path.is_relative_to(inventory.root):
            raise AssertionError(f"source reread on cache hit: {path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", cache_only_read)
    again = build_structure(inventory, output, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)
    assert again["cache"]["hit"] is True
    assert again["counts"] == coverage["counts"]
    next_snapshot = replace(
        inventory, project="renamed-project", snapshot_id="snapshot-2"
    )
    build_structure(next_snapshot, output, cache_dir=tmp_path / "cache")
    cached_symbols = _jsonl(output / "structure/symbols.jsonl")
    assert all(row["project"] == "renamed-project" for row in cached_symbols)
    assert all(row["snapshot_id"] == "snapshot-2" for row in cached_symbols)


def test_inventory_hash_mismatch_is_coverage_gap_not_metrics(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path, {"main.py": ("def f(): pass\n", "implementation")})
    bad_file = replace(inventory.files[0], sha256="0" * 64)
    inventory = replace(inventory, files=(bad_file,))
    output = tmp_path / "package"
    import pytest

    with pytest.raises(ValueError, match="main.py: captured hash mismatch"):
        build_structure(inventory, output)
    assert not (output / "structure").exists()


def test_import_graph_src_layout_absolute_relative_and_cycles(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path,
        {
            "src/pkg/__init__.py": ("", "implementation"),
            "src/pkg/base.py": ("VALUE = 1\n", "implementation"),
            "src/pkg/sub/__init__.py": ("", "implementation"),
            "src/pkg/sub/a.py": (
                "from pkg import base\nfrom . import b\nfrom .. import base as parent_base\n",
                "implementation",
            ),
            "src/pkg/sub/b.py": ("from . import a\n", "implementation"),
        },
    )
    output = tmp_path / "package"
    build_structure(inventory, output)
    projections = json.loads((output / "structure/graph_projections.json").read_text())
    assert projections["edge_dataset"] == "dependency_edges.jsonl"
    assert projections["projections"]["all_static_imports"]["count"] == 4
    assert not (output / "structure/all_static_imports.jsonl").exists()
    imports = _jsonl(output / "structure/imports.jsonl")
    a_imports = [row for row in imports if row["path"] == "src/pkg/sub/a.py"]
    assert {row["target_module"] for row in a_imports} == {"pkg.base", "pkg.sub.b"}
    nodes = _jsonl(output / "structure/graph_nodes.jsonl")
    assert {row["node"] for row in nodes} == {
        "pkg",
        "pkg.base",
        "pkg.sub",
        "pkg.sub.a",
        "pkg.sub.b",
    }
    cycles = _jsonl(output / "structure/cycles.jsonl")
    assert cycles == [
        {
            "project": "fixture",
            "snapshot_id": "snapshot-1",
            "component_id": "scc-0001",
            "nodes": ["pkg.sub.a", "pkg.sub.b"],
            "node_count": 2,
            "internal_edge_count": 2,
        }
    ]
    graph_csv = (output / "structure/graph_metrics.csv").read_text()
    assert "pkg.sub.a" in graph_csv and "pkg.sub.b" in graph_csv


def test_portfolio_links_use_explicit_local_paths_not_dependency_names(
    tmp_path: Path,
) -> None:
    from lynchpin.sources.chisel_structure import build_portfolio_links

    source_path = tmp_path / "repos" / "source"
    target_path = tmp_path / "repos" / "target"
    source = _inventory(
        tmp_path / "source-fixture",
        {
            "Cargo.toml": (
                "[package]\nname='same'\nversion='0.1.0'\n"
                "[dependencies]\nsame='1'\nother={path='../target', package='same'}\n",
                "tooling",
            ),
        },
        project="source",
    )
    target = _inventory(
        tmp_path / "target-fixture",
        {"Cargo.toml": ("[package]\nname='same'\nversion='0.1.0'\n", "tooling")},
        project="target",
    )
    # Rewrite captured roots to independent checkout paths used in plan resolution.
    source_plan = SimpleNamespace(name="source", path=source_path, github_slug=None)
    target_plan = SimpleNamespace(
        name="target", path=target_path, github_slug="Org/target"
    )
    portfolio_root = tmp_path / "portfolio"
    for plan, inventory, snapshot in (
        (source_plan, source, "source-snap"),
        (target_plan, target, "target-snap"),
    ):
        project_dir = portfolio_root / plan.name
        build_structure(inventory, project_dir)
        (project_dir / "capture.json").write_text(json.dumps({"snapshot_id": snapshot}))
    coverage = build_portfolio_links(portfolio_root, [source_plan, target_plan])
    links = _jsonl(portfolio_root / "cross-project-links.jsonl")
    assert coverage["resolved_links"] == 1
    assert links[0]["source_project"] == "source"
    assert links[0]["target_project"] == "target"
    assert links[0]["target_snapshot_id"] == "target-snap"
    assert links[0]["source_line"] == 6
