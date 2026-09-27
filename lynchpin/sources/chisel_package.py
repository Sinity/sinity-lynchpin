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
from typing import Any, Callable

from .chisel_cache import copy_file


# Repomix 1.18.0 does not include these textual fixture/capture extensions in
# its XML file list. They remain available byte-for-byte in source/ and the
# working tree archive, and are recorded explicitly in representations/*.json.
_REPOMIX_TEXT_FILTER_SUFFIXES = frozenset({".snap", ".raw", ".key"})


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
    binary: set[str] = set()
    empty: set[str] = set()
    filtered_text: set[str] = set()
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
            else:
                if Path(path).suffix.lower() in _REPOMIX_TEXT_FILTER_SUFFIXES:
                    filtered_text.add(path)
    unexplained = omitted - binary - empty - filtered_text
    if actual - expected or unexplained:
        raise ValueError(f"{name} membership mismatch: missing={sorted(unexplained)!r}, "
                         f"unexpected={sorted(actual-expected)!r}")
    view_dir = out_dir / "representations"
    view_dir.mkdir(exist_ok=True)
    (view_dir / f"{name}.json").write_text(json.dumps({
        "snapshot_id": inventory.snapshot_id, "artifact": output.name,
        "members": sorted(expected), "represented": sorted(actual),
        "binary_raw_only": sorted(binary), "empty_raw_only": sorted(empty),
        "repomix_filtered_text_raw_only": sorted(filtered_text),
        "compressed": compressed,
        "method": "exact captured file tree; compressed view is structural and lossy",
    }, indent=2) + "\n", encoding="utf-8")
    return result


def captured_sidecars(plan: Any, inventory: Any, out_dir: Path,
                      log: list[str]) -> tuple[list[str], int]:
    """Keep Git's complete history and archive exactly the captured source tree."""
    from . import chisel

    bundle = out_dir / f"{plan.name}-all-refs.bundle"
    refs = chisel._run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=plan.path)
    head = chisel._run(["git", "rev-parse", "HEAD"], cwd=plan.path)
    if refs.returncode or head.returncode:
        raise RuntimeError("cannot identify frozen history refs")
    key = hashlib.sha256(("git-bundle-v1\n" + refs.stdout + head.stdout).encode()).hexdigest()
    cache = out_dir.parent.parent / ".chisel-cache" / plan.name / "bundles" / key
    cached = cache / "history.bundle"
    checksum = cache / "sha256"
    hit = cached.exists() and checksum.exists() and hashlib.file_digest(cached.open("rb"), "sha256").hexdigest() == checksum.read_text().strip()
    if hit:
        copy_file(cached, bundle)
    else:
        result = chisel._run(["git", "bundle", "create", str(bundle), "--all"], cwd=plan.path)
        if result.returncode:
            raise RuntimeError(result.stderr or "git bundle failed")
        cache.mkdir(parents=True, exist_ok=True)
        copy_file(bundle, cached)
        checksum.write_text(hashlib.file_digest(cached.open("rb"), "sha256").hexdigest() + "\n")
    chisel._emit(log, f"  history bundle cache: {'hit' if hit else 'miss'} ({key[:12]})")
    archive = out_dir / f"{plan.name}-working-tree.tar.gz"
    from .chisel_options import active_options

    if active_options.xml:
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(inventory.root, arcname=plan.name)
    tree = out_dir / f"{plan.name}-repo-tree.txt"
    tree.write_text("\n".join(row.path for row in inventory.files if row.included) + "\n",
                    encoding="utf-8")
    paths = [p for p in (bundle, archive, tree) if p.exists()]
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
    actual: dict[str, str] = {}
    try:
        for line in result.stdout.splitlines():
            object_id, separator, name = line.partition(" ")
            if not separator or not object_id or not name or name in actual:
                raise ValueError("malformed or duplicate bundle head")
            actual[name] = object_id
    except ValueError as exc:
        raise RuntimeError("cannot verify Git bundle refs: malformed bundle heads") from exc
    head = actual.pop("HEAD", None)
    history_coverage = out_dir / "history/coverage.json"
    expected_head = json.loads(history_coverage.read_text()).get("head_at_capture") if history_coverage.exists() else inventory.revision
    # `git bundle create --all` includes linked-worktree HEAD pseudorefs. They
    # are extra views of checked-out commits, not refs from the captured
    # history inventory. Continue to require every true ref and the main HEAD
    # to match exactly, and reject every other unexpected bundle name.
    worktree_heads = {name: value for name, value in actual.items()
                      if name.startswith("worktrees/") and name.endswith("/HEAD")}
    actual_without_worktree_heads = {
        name: value for name, value in actual.items() if name not in worktree_heads
    }
    if (actual_without_worktree_heads != expected
            or head is None or head != expected_head):
        raise RuntimeError("Git bundle refs do not match captured history refs")
    capture_path = out_dir / "capture.json"
    capture = json.loads(capture_path.read_text())
    capture["history_refs_sha256"] = hashlib.sha256(refs_path.read_bytes()).hexdigest()
    capture["snapshot_id_scope"] = "captured source bytes, roles and selection policy; manifest hash identifies complete package"
    capture_path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")


def evidence_outputs(plan: Any, inventory: Any, out_dir: Path, cache_dir: Path,
                     log: list[str] | None = None,
                     *, report_builder: Callable[..., Any] | None = None) -> list[dict]:
    """Derive navigation from captured bytes and owner-exported records."""
    from . import chisel
    from .chisel_context import build_context
    from .chisel_offline import build_offline_package
    from .chisel_compact import compact_jsonl
    from .chisel_structure import build_structure
    from .chisel_snapshots import build_overlay_views
    from .chisel_options import active_options
    from .chisel_excerpts import collect_excerpts

    if report_builder is None and "source" in active_options.datasets:
        raise ValueError("Chisel source reports require the analysis-layer report builder")

    items = [item for key, values in (chisel._github_context_index or {}).items()
             if key[0] == plan.name for item in values]
    tracker_dir = out_dir / "trackers"
    tracker_dir.mkdir(exist_ok=True)
    materialization = chisel._github_context_manifest or None
    project_inventory_coverage = (
        (materialization or {}).get("inventory_coverage", {}).get(plan.name, {})
        if isinstance(materialization, dict) else {}
    )
    (tracker_dir / "github-coverage.json").write_text(json.dumps({
        "project": plan.name, "repository": plan.github_slug,
        "exported_records": len(items),
        "inventory_coverage": project_inventory_coverage,
        "materialization": materialization,
        "interpretation": "Exported record counts are observed rows. An inventory at its requested limit may be truncated; its total is unknown. Missing materialization or stale fallback does not establish current zero issues or PRs.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    steps = [
        ("structure", lambda: build_structure(inventory, out_dir, cache_dir=cache_dir / "structure")),
        ("overlay-views", lambda: build_overlay_views(inventory, out_dir, cache_dir, active_options.datasets)),
        ("owner-evidence", lambda: build_context(plan.path, out_dir, project=plan.name,
            revision=inventory.revision, dirty=inventory.dirty, github_items=items,
            github_slug=plan.github_slug)),
        ("context", lambda: collect_excerpts(plan.path, out_dir, active_options)),
        ("reports", lambda: report_builder(out_dir, project=plan.name,
            task_roots=[root for project, root in active_options.task_roots if project == plan.name])),
        ("compact", lambda: compact_jsonl(out_dir)),
        ("offline-index", lambda: build_offline_package(out_dir, project=plan.name,
            snapshot_id=inventory.snapshot_id, generated_at=inventory.generated_at)),
    ]
    timings = []
    for name, run in steps:
        dataset = {"owner-evidence": "execution", "offline-index": "source", "reports": "source", "overlay-views": "structure"}.get(name, name)
        if name == "owner-evidence" and "trackers" in active_options.datasets:
            pass
        elif name == "compact":
            pass
        elif name == "overlay-views" and "metrics" in active_options.datasets:
            pass
        elif dataset not in active_options.datasets:
            continue
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        chisel._set_stage(plan.name, name, True)
        chisel._emit(log, f"  → {plan.name}: {name}")
        try:
            run()
        finally:
            chisel._set_stage(plan.name, name, False)
        elapsed = round(time.perf_counter() - start, 3)
        timings.append({"stage": name, "label": plan.name, "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_s": elapsed, "queue_wait_s": 0.0})
        chisel._emit(log, f"  ✓ {plan.name}: {name} ({elapsed:.1f}s)")
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
    (root / "ATTACHMENT_START_HERE.md").write_text(
        "# Chisel attachments\n\nExtract the selected attachments together. "
        "Each archive's navigation and verified contents are listed under `attachments/`. "
        "Those manifests name the actual companion files and snapshot identities. "
        "Numbered parts include their reconstruction requirements under `parts/`; "
        "run `python3 reconstruct.py` after extracting them. Start browsing with "
        "`portfolio.json` and each project's `START_HERE.md`.\n")
    (root / "attachment-profile.json").unlink(missing_ok=True)
    from .chisel_attachments import build_attachments
    from .chisel_options import active_options

    manifest = build_attachments(root, [p.name for p in plans],
                                 limit=active_options.attachment_bytes,
                                 layout=active_options.attachment_layout)
    return manifest["attachments"][0]["path"] if len(manifest["attachments"]) == 1 else "attachments.json"
