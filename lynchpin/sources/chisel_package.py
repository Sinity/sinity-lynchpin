"""Build equivalent source views from Chisel's captured inventory."""

from __future__ import annotations

import json
import hashlib
import os
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any

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
