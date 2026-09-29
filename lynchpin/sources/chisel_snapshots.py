"""Local Git identities and immutable source captures, independent of checkout moves."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chisel_inventory import CapturedInventory, _git_state, capture_inventory

SCHEMA_VERSION = 1


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout.strip()


def resolve_ref(repo: Path, ref: str | None = None) -> tuple[str, str]:
    """Return ``(ref, commit)`` for a local ref, or for the default branch when ``ref`` is None."""
    if ref is None:
        try:
            ref = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
        except subprocess.CalledProcessError:
            for candidate in ("refs/heads/main", "refs/heads/master"):
                try:
                    git(repo, "rev-parse", "--verify", candidate)
                except subprocess.CalledProcessError:
                    continue
                ref = candidate
                break
        if ref is None:
            raise ValueError(f"default branch identity unavailable: {repo}")
    return ref, git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")


def resolve_snapshot(repo: Path, ref: str | None = None) -> dict[str, Any]:
    """Resolve only local refs. FETCH_HEAD time is not a remote freshness claim."""
    ref, revision = resolve_ref(repo, ref)
    return {"ref": ref, "revision": revision,
            "commit_time": git(repo, "show", "-s", "--format=%cI", revision),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "local refs only; remote freshness unknown",
            "network_checked_at": None}


def capture_commit(plan: Any, destination: Path, identity: dict[str, Any], **kwargs: Any) -> CapturedInventory:
    """Read pinned Git blobs in one batch without checkout or archive attributes."""
    revision = identity["revision"]
    listing = subprocess.run(["git", "ls-tree", "-rz", "--full-tree", revision],
                             cwd=plan.path, check=True, capture_output=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
    entries = []
    for item in listing.split(b"\0"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, kind, oid = header.split()
        if kind == b"blob":
            entries.append((mode, oid, os.fsdecode(path)))
    request = b"".join(oid + b"\n" for _, oid, _ in entries)
    result = subprocess.run(["git", "cat-file", "--batch"], input=request,
                            cwd=plan.path, check=True, capture_output=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
    with tempfile.TemporaryDirectory(prefix=".git-source-", dir=destination.parent) as temporary:
        root = Path(temporary)
        offset = 0
        for mode, oid, relative in entries:
            end = result.index(b"\n", offset)
            actual_oid, kind, size = result[offset:end].split()
            if actual_oid != oid or kind != b"blob":
                raise ValueError("unexpected Git object batch response")
            offset = end + 1
            data = result[offset:offset + int(size)]
            offset += int(size) + 1
            path = root / relative
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("unsafe Git tree path")
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == b"120000":
                path.symlink_to(os.fsdecode(data))
            else:
                path.write_bytes(data)
                path.chmod(int(mode, 8) & 0o777)
        return capture_inventory(replace(plan, path=root), destination,
                                 committed_paths=tuple(p for _, _, p in entries),
                                 committed_revision=revision, **kwargs)


def _overlay(primary: CapturedInventory, other: CapturedInventory, root: Path) -> dict[str, Any]:
    base = {r.path: r for r in primary.files if r.included}
    selected = {r.path: r for r in other.files if r.included}
    changed = []
    for path, record in selected.items():
        previous = base.get(path)
        if previous is None or (previous.sha256, previous.mode) != (record.sha256, record.mode):
            target = root / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(other.root / path, target)
            changed.append(path)
    manifest = {"schema_version": SCHEMA_VERSION, "snapshot_id": other.snapshot_id,
                "base_snapshot_id": primary.snapshot_id, "revision": other.revision,
                "dirty": other.dirty, "captured_at": other.generated_at,
                "changed": changed, "deleted": sorted(base.keys() - selected.keys()),
                "files": [asdict(r) for r in other.files]}
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def capture_catalogue(plan: Any, destination: Path, options: Any, **kwargs: Any) -> CapturedInventory:
    primary_identity = resolve_snapshot(plan.path)
    default_identity = primary_identity.copy()
    refs = [ref for project, ref in options.refs if project == plan.name]
    identities = [resolve_snapshot(plan.path, ref) for ref in refs]
    activation = None
    if plan.name == "sinnix" and "execution" in options.datasets:
        from .chisel_execution import activation_snapshot

        activation = activation_snapshot(days=options.context_days)
        (destination / "verification").mkdir(parents=True, exist_ok=True)
        (destination / "verification/activation.json").write_text(json.dumps(activation, default=str, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix=".snapshots-", dir=destination.parent) as temporary:
        temp = Path(temporary)
        worktree = capture_inventory(plan, temp / "worktree", **kwargs)
        if options.target == "worktree":
            shutil.copytree(temp / "worktree", destination, dirs_exist_ok=True)
            primary = replace(worktree, root=destination / "source")
            primary_identity = {"ref": "worktree", "revision": worktree.revision,
                                "freshness": "captured checkout", "network_checked_at": None}
        else:
            primary = capture_commit(plan, destination, primary_identity, **kwargs)
        snapshots = [{"name": "primary", **primary_identity, "snapshot_id": primary.snapshot_id,
                      "dirty": primary.dirty, "path": "source/"}]
        if options.target == "worktree":
            merged = capture_commit(plan, temp / "merged", default_identity, **kwargs)
            _overlay(primary, merged, destination / "snapshots/merged")
            snapshots.append({"name": "merged", **default_identity, "snapshot_id": merged.snapshot_id,
                              "path": "snapshots/merged/manifest.json"})
        overlay = _overlay(primary, worktree, destination / "snapshots/worktree")
        snapshots.append({"name": "worktree", "snapshot_id": worktree.snapshot_id,
                          "revision": worktree.revision, "dirty": worktree.dirty,
                          "path": "snapshots/worktree/manifest.json"})
        for index, identity in enumerate(identities):
            name = f"candidate-{index + 1}"
            candidate = capture_commit(plan, temp / name, identity, **kwargs)
            _overlay(primary, candidate, destination / "snapshots" / name)
            snapshots.append({"name": name, **identity, "snapshot_id": candidate.snapshot_id,
                              "dirty": candidate.dirty,
                              "path": f"snapshots/{name}/manifest.json"})
        if activation and activation.get("last_activated"):
            revision = activation["last_activated"]["sinnix_revision"]
            try:
                identity = resolve_snapshot(plan.path, revision)
                activated = capture_commit(plan, temp / "last-activated", identity, **kwargs)
                _overlay(primary, activated, destination / "snapshots/last-activated")
                snapshots.append({"name": "last-activated", **identity, "snapshot_id": activated.snapshot_id,
                                  "path": "snapshots/last-activated/manifest.json", "owner": "sinnix_generation_log"})
            except (ValueError, subprocess.SubprocessError):
                snapshots.append({"name": "last-activated", "revision": revision, "snapshot_id": None,
                                  "coverage": "revision not available in local Git objects"})
        catalogue = {"schema_version": SCHEMA_VERSION, "primary": primary.snapshot_id,
                     "snapshots": snapshots, "installed": {"revision": None, "coverage": "owner evidence unavailable"},
                     "worktree_changes": len(overlay["changed"]),
                     "coherence": "worktree checked during capture; committed bytes read from pinned objects"}
        (destination / "snapshots.json").write_text(json.dumps(catalogue, indent=2) + "\n")
    verify_snapshot(primary)
    return primary


def verify_snapshot(inventory: CapturedInventory) -> None:
    for record in inventory.files:
        if record.included:
            path = inventory.root / record.path
            if hashlib.sha256(path.read_bytes()).hexdigest() != record.sha256:
                raise ValueError(f"captured snapshot hash mismatch: {record.path}")


def _dirty_paths(repo: Path) -> list[str]:
    # Not ``git()``: stripping would eat the status column of the first entry.
    raw = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                         cwd=repo, check=True, capture_output=True, text=True,
                         env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
    paths: list[str] = []
    entries = iter(raw.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            paths.append(next(entries, ""))
    return [path for path in paths if path]


def _overlay_current(repo: Path, package: Path, primary: dict[str, Any]) -> str | None:
    """Return why a captured checkout view no longer matches the checkout, or None.

    The Git status fingerprint catches changed dirty membership; only the paths
    Git reports as dirty are hashed, so clean tracked files cost nothing.
    """
    capture = json.loads((package / "capture.json").read_text())
    worktree = next((s for s in json.loads((package / "snapshots.json").read_text())["snapshots"]
                     if s.get("name") == "worktree"), None)
    if worktree is None:
        return "retained checkout view has no worktree overlay manifest"
    revision, dirty, fingerprint = _git_state(repo)
    if revision != primary.get("revision"):
        return f"checkout HEAD {revision} differs from captured {primary.get('revision')}"
    if (dirty, fingerprint) != (capture.get("dirty"), capture.get("status_fingerprint")):
        return "checkout dirty-file set changed since capture"
    records = {row["path"]: row for row in
               json.loads((package / worktree["path"]).read_text())["files"]}
    for relative in _dirty_paths(repo):
        record = records.get(relative)
        path = repo / relative
        exists = path.is_file() and not path.is_symlink()
        if record is None:
            if exists:
                return f"dirty path was not captured: {relative}"
            continue
        if not record.get("included") or record.get("sha256") is None:
            continue
        if not exists or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            return f"dirty path changed since capture: {relative}"
    return None


def view_currentness(repo: Path, package: Path) -> dict[str, Any]:
    """Compare a retained package's selected views with the repository's local refs.

    Default-branch views follow the default ref's commit; explicit candidate
    refs follow only their own ref. Only a checkout-target view (primary ref
    ``worktree``) includes the dirty overlay, so live edits never invalidate a
    commit-pinned view. Git resolves linked worktrees, so ``.git`` may be a file.
    """
    catalogue_path = package / "snapshots.json"
    if not catalogue_path.is_file():
        return {"state": "never_captured", "reason": f"no retained snapshot catalogue at {catalogue_path}"}
    try:
        git(repo, "rev-parse", "--git-dir")
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"state": "unavailable", "reason": f"repository unavailable: {repo}: {exc}"}
    try:
        snapshots = json.loads(catalogue_path.read_text())["snapshots"]
    except (OSError, ValueError, KeyError) as exc:
        return {"state": "stale", "reason": f"retained snapshot catalogue unreadable: {exc}"}
    stale: list[str] = []
    for snapshot in snapshots:
        name = snapshot.get("name")
        try:
            if name == "primary" and snapshot.get("ref") == "worktree":
                reason = _overlay_current(repo, package, snapshot)
                if reason:
                    stale.append(f"primary: {reason}")
                continue
            if name == "primary" or name == "merged":
                ref, revision = resolve_ref(repo)
            elif str(name).startswith("candidate-"):
                ref, revision = resolve_ref(repo, snapshot["ref"])
            else:
                continue
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
            stale.append(f"{name}: selected view unavailable: {exc}")
            continue
        if (ref, revision) != (snapshot.get("ref"), snapshot.get("revision")):
            stale.append(f"{name}: {ref}@{revision[:12]} differs from captured "
                         f"{snapshot.get('ref')}@{str(snapshot.get('revision'))[:12]}")
    primary = snapshots[0] if snapshots else {}
    if stale:
        return {"state": "stale", "reason": "; ".join(stale), "revision": primary.get("revision")}
    return {"state": "current", "reason": "selected views match local refs",
            "revision": primary.get("revision")}
