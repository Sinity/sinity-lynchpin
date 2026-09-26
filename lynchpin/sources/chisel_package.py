"""Build equivalent source views from Chisel's captured inventory."""

from __future__ import annotations

import json
import hashlib
import os
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_view(
    repomix_bin: str, out_dir: Path, plan: Any, inventory: Any,
    name: str, git: dict, generated_at: str, log: list[str],
    *, compressed: bool = False,
) -> tuple[str, int]:
    """Give Repomix an exact tree, disabling its independent path policies."""
    from . import chisel

    members = inventory.memberships.get(name, ())
    if name in ("scratchpad", "accelerants"):
        members = tuple(row.path for row in inventory.files
                        if row.included and name in row.included_by)
    if compressed:
        members = tuple(sorted({p for s in plan.slices
                                for p in inventory.memberships.get(s.name, ())}))
    output = out_dir / f"{plan.name}-{name}.xml"
    with tempfile.TemporaryDirectory(prefix=".view-", dir=out_dir.parent) as tmp:
        root = Path(tmp)
        for relative in members:
            source = inventory.root / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
        # An explicit external configuration prevents a captured repository's
        # Repomix configuration from silently changing the selected contents.
        config = root.parent / f"{root.name}.json"
        config.write_text("{}\n", encoding="utf-8")
        try:
            args = ["--style", "xml", "--parsable-style", "--quiet",
                    "--no-security-check", "--no-gitignore", "--no-dot-ignore",
                    "--no-default-patterns", "--no-git-sort-by-changes",
                    "--config", str(config), "--output", str(output),
                    "--header-text", f"Project: {plan.name}; snapshot: {inventory.snapshot_id}; "
                    f"view: {name}. Raw bytes and hashes: source/ and inventory.jsonl."]
            if compressed:
                args += ["--compress"]
            else:
                args += ["--output-show-line-numbers"]
            result = chisel._run_repomix(
                repomix_bin, output, replace(plan, path=root), args,
                git, generated_at, log,
            )
        finally:
            config.unlink(missing_ok=True)
    actual = {node.attrib["path"] for node in ET.parse(output).iter("file")
              if "path" in node.attrib}
    expected = set(members)
    # Repomix omits binary files. Keep them in source/ and state this precisely.
    omitted = expected - actual
    binary = set()
    empty = set()
    for path in omitted:
        data = (inventory.root / path).read_bytes()
        if not data:
            empty.add(path)
        elif b"\0" in data:
            binary.add(path)
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                binary.add(path)
    unexplained = omitted - binary - empty
    if actual - expected or unexplained:
        raise ValueError(f"{name} membership mismatch: missing={sorted(unexplained)!r}, "
                         f"unexpected={sorted(actual-expected)!r}")
    view_dir = out_dir / "representations"
    view_dir.mkdir(exist_ok=True)
    (view_dir / f"{name}.json").write_text(json.dumps({
        "snapshot_id": inventory.snapshot_id, "artifact": output.name,
        "members": sorted(expected), "represented": sorted(actual),
        "binary_raw_only": sorted(binary), "empty_raw_only": sorted(empty),
        "compressed": compressed,
        "method": "exact captured file tree; compressed view is structural and lossy",
    }, indent=2) + "\n", encoding="utf-8")
    return result


def captured_sidecars(plan: Any, inventory: Any, out_dir: Path,
                      log: list[str]) -> tuple[list[str], int]:
    """Keep Git's complete history and archive exactly the captured source tree."""
    from . import chisel

    bundle = out_dir / f"{plan.name}-all-refs.bundle"
    result = chisel._run(["git", "bundle", "create", str(bundle), "--all"], cwd=plan.path)
    if result.returncode:
        raise RuntimeError(result.stderr or "git bundle failed")
    archive = out_dir / f"{plan.name}-working-tree.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(inventory.root, arcname=plan.name)
    tree = out_dir / f"{plan.name}-repo-tree.txt"
    tree.write_text("\n".join(row.path for row in inventory.files if row.included) + "\n",
                    encoding="utf-8")
    paths = [bundle, archive, tree]
    return [p.name for p in paths], sum(p.stat().st_size for p in paths)


def verify_history_bundle(plan: Any, inventory: Any, out_dir: Path) -> None:
    """Reject a bundle from a different ref observation than the history index."""
    from . import chisel

    refs_path = out_dir / "history/refs.jsonl"
    expected = {row["name"]: row["object"] for row in
                (json.loads(line) for line in refs_path.read_text().splitlines() if line)}
    bundle = out_dir / f"{plan.name}-all-refs.bundle"
    result = chisel._run(["git", "bundle", "list-heads", str(bundle)], cwd=plan.path)
    if result.returncode:
        raise RuntimeError(f"cannot verify Git bundle refs: {result.stderr}")
    actual = dict(line.split(" ", 1)[::-1] for line in result.stdout.splitlines())
    head = actual.pop("HEAD", None)
    if actual != expected or (head is not None and head != inventory.revision):
        raise RuntimeError("Git bundle refs do not match captured history refs")
    capture_path = out_dir / "capture.json"
    capture = json.loads(capture_path.read_text())
    capture["history_refs_sha256"] = hashlib.sha256(refs_path.read_bytes()).hexdigest()
    capture["snapshot_id_scope"] = "captured source bytes, roles and selection policy; manifest hash identifies complete package"
    capture_path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")


def evidence_outputs(plan: Any, inventory: Any, out_dir: Path, cache_dir: Path) -> list[dict]:
    """Derive navigation from captured bytes and owner-exported records."""
    from . import chisel
    from .chisel_context import build_context
    from .chisel_offline import build_offline_package
    from .chisel_structure import build_structure

    items = [item for key, values in (chisel._github_context_index or {}).items()
             if key[0] == plan.name for item in values]
    tracker_dir = out_dir / "trackers"
    tracker_dir.mkdir(exist_ok=True)
    (tracker_dir / "github-coverage.json").write_text(json.dumps({
        "project": plan.name, "repository": plan.github_slug,
        "exported_records": len(items),
        "materialization": chisel._github_context_manifest or None,
        "interpretation": "Record count describes this export. Missing materialization or stale fallback does not establish current zero issues or PRs.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    steps = [
        ("structure", lambda: build_structure(inventory, out_dir, cache_dir=cache_dir / "structure")),
        ("owner-evidence", lambda: build_context(plan.path, out_dir, project=plan.name,
            revision=inventory.revision, dirty=inventory.dirty, github_items=items,
            github_slug=plan.github_slug)),
        ("offline-index", lambda: build_offline_package(out_dir, project=plan.name,
            snapshot_id=inventory.snapshot_id, generated_at=inventory.generated_at)),
    ]
    timings = []
    for name, run in steps:
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        chisel._print_live(f"  → {plan.name}: {name}")
        run()
        elapsed = round(time.perf_counter() - start, 3)
        timings.append({"stage": name, "label": plan.name, "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_s": elapsed, "queue_wait_s": 0.0})
        chisel._print_live(f"  ✓ {plan.name}: {name} ({elapsed:.1f}s)")
    return timings


def build_portfolio(root: Path, plans: Any, results: dict, generated_at: str) -> str:
    """One extraction exposes every selected project and its exact capture ID."""
    from .chisel_structure import build_portfolio_links

    build_portfolio_links(root, plans)
    projects = [{"project": p.name, "snapshot_id": results[p.name]["snapshot_id"],
                 "manifest_sha256": hashlib.sha256(
                     (root / p.name / f"{p.name}-manifest.json").read_bytes()).hexdigest(),
                 "captured_at": json.loads((root / p.name / "capture.json").read_text())["generated_at"],
                 "git": results[p.name]["git"], "path": p.name} for p in plans]
    (root / "portfolio.json").write_text(json.dumps({
        "generated_at": generated_at, "projects": projects,
        "method": "comparisons use each listed capture; capture times may differ",
    }, indent=2) + "\n", encoding="utf-8")
    (root / "START_HERE.md").write_text(
        "# Chisel portfolio\n\nEach project directory contains START_HERE.md, raw source, "
        "evidence tables and a Python standard-library browse.py helper.\n\n"
        "portfolio.json binds the selection to exact snapshot IDs. growth/ contains "
        "comparative measurements with their methods. Project context and documentation "
        "are excluded from maintained code counts. Test source share is not coverage.\n",
        encoding="utf-8",
    )
    target = root / "portfolio-all.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for p in plans:
            tar.add(root / p.name, arcname=p.name)
        for name in ("portfolio.json", "START_HERE.md", "index.json", "index.md", "growth",
                     "cross-project-links.jsonl", "cross-project-links.coverage.json"):
            path = root / name
            if path.exists():
                tar.add(path, arcname=name)
    return target.name
