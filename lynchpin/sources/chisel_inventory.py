"""Captured source inventory for reproducible Chisel packages.

Package membership and analytical role are deliberately separate: a file can
be preserved as context without contributing to maintained-code measures.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

POLICY_VERSION = "chisel-role-policy-5"
ROLES = frozenset({"implementation", "tests", "tooling", "documentation", "context", "evidence", "unclassified"})


@dataclass(frozen=True)
class InventoryFile:
    path: str
    sha256: str | None
    size_bytes: int | None
    role: str
    role_reason: str
    included_by: tuple[str, ...]
    excluded_by: tuple[str, ...]
    source_kind: str
    included: bool
    symlink_target: str | None = None
    mode: int | None = None
    purpose: str = "unknown"
    material: str = "unknown"
    component: str = "unknown"
    classification_reason: str = ""


@dataclass(frozen=True)
class CapturedInventory:
    root: Path
    files: tuple[InventoryFile, ...]
    memberships: dict[str, tuple[str, ...]]
    project: str
    generated_at: str
    revision: str | None
    dirty: bool | None
    policy_version: str
    snapshot_id: str
    status_fingerprint: str | None = None


def _glob(path: str, pattern: str) -> bool:
    path = path.removeprefix("./")
    pattern = pattern.removeprefix("./")
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in path.split("/"))
    parts, pats = path.split("/"), pattern.split("/")

    def match(i: int, j: int) -> bool:
        if j == len(pats):
            return i == len(parts)
        if pats[j] == "**":
            return any(match(k, j + 1) for k in range(i, len(parts) + 1))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], pats[j]) and match(i + 1, j + 1)

    return match(0, 0) or (pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"))


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_glob(path, p) for p in patterns)


def classify_role(path: str, *, project: str = "") -> tuple[str, str]:
    """Classify a repository path. Unrecognized paths never become production."""
    p = path.replace("\\", "/").removeprefix("./")
    lower = p.lower()
    parts = lower.split("/")
    name = parts[-1] if parts else lower
    if len(parts) == 1 and Path(name).stem in {"scratch", "scratchpad"}:
        return "context", "project-root scratch file"
    if any(x in {"scratch", "scratchpads", "planning", "handoffs", "history-summaries", "notes", "demos", "prompts"} for x in parts) or name in {"scratchpad.md", "current.md", "memory.md"}:
        return "context", "project-local notes, scratch, planning, or agent context"
    if parts[0] in {".beads", ".claude", ".serena"} or ".agent" in parts:
        if ".agent" in parts and "scripts" in parts:
            return "tooling", "explicit agent operational script"
        return "context", "agent-local context or coordination material"
    if p.startswith(("docs/", "doc/")) or name.endswith((".md", ".rst", ".adoc")):
        return "documentation", "documentation file or documentation directory"
    lockfiles = {
        "cargo.lock", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "pnpm-lock.yml",
        "yarn.lock", "yarn.lockb", "poetry.lock", "uv.lock", "pdm.lock", "pipfile.lock",
        "flake.lock", "go.sum", "gemfile.lock", "composer.lock", "mix.lock", "bun.lock",
        "bun.lockb", "deno.lock", "gradle.lockfile", "packages.lock.json", "gopkg.sum",
    }
    if name in lockfiles:
        return "evidence", "generated dependency lock or checksum metadata"
    if any(x in parts for x in ("fixtures", "golden", "captures", "testdata", "test-data", "vendor", "generated")):
        return "evidence", "fixture, generated, vendor, or captured evidence"
    if project == "polylogue" and parts[0] == "devtools" and name.endswith(".py"):
        if name.startswith("test_") or "tests" in parts:
            return "tests", "Polylogue devtools test source"
        return "tooling", "Polylogue devtools verification or development command"
    if name.startswith(("test_",)) or name.endswith(("_test.py", "_tests.py", "_test.rs", "_tests.rs")) or "tests" in parts or "test" in parts:
        return "tests", "test source by conventional path or filename"
    if name in {"readme", "readme.md", "readme.rst", "changelog.md", "contributing.md", "license", "license.md"}:
        return "documentation", "documentation file or documentation directory"
    if name in {".ignore", ".tokeignore"} or p.startswith(("hosts/", "modules/", "scripts/", ".github/workflows/")):
        return "tooling", "build, host, workflow, or operational tooling path"
    if (name.startswith(("cargo.", "package.json", "package-lock.", "pnpm-lock.", "yarn.lock", "poetry.lock", "uv.lock", "pyproject.toml", "requirements", "flake.", "justfile", "makefile", "dockerfile")) or name in {"cargo.toml", "cargo.lock", "go.mod", "go.sum", "composer.json", "gemfile", "gemfile.lock", "makefile", "justfile"} or name.endswith((".nix", ".toml", ".yaml", ".yml", ".sh", ".bash", ".ps1", ".mk")) and ("script" in parts or name in {"flake.nix", "default.nix", "shell.nix"})):
        return "tooling", "build, dependency, deployment, or operational configuration"
    if name.endswith((".py", ".rs", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala", ".ex", ".exs")):
        return "implementation", "recognized implementation source file"
    return "unclassified", "no explicit role rule matched"


_SOURCE_SUFFIXES = {".py", ".rs", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala", ".ex", ".exs", ".sh", ".bash", ".zsh", ".fish", ".ps1"}
_CONFIG_SUFFIXES = {".nix", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".service", ".timer"}


def classify_dimensions(path: str, *, role: str | None = None, binary: bool = False) -> dict[str, str]:
    role, reason = (role, "inventory role") if role is not None else classify_role(path)
    parts = Path(path.lower()).parts
    name = parts[-1]
    suffix = Path(name).suffix
    purpose = {"implementation": "production", "tests": "tests", "tooling": "build/verification tooling", "documentation": "documentation", "context": "context", "evidence": "evidence"}.get(role, "unknown")
    if binary:
        material = "binary"
    elif "vendor" in parts or "vendored" in parts:
        material = "vendored"
    elif any(p in parts for p in ("fixtures", "golden", "testdata", "test-data", "captures")):
        material = "fixture"
    elif "generated" in parts or name.endswith(".lock") or name in {"package-lock.json", "go.sum"}:
        material = "generated"
    elif suffix in _SOURCE_SUFFIXES or name in {"justfile", "makefile", "dockerfile"}:
        material = "source"
    elif suffix in _CONFIG_SUFFIXES or name in {".ignore", ".tokeignore"}:
        material = "configuration"
    else:
        material = "unknown"
    if suffix == ".nix" and parts[0] in {"modules", "hosts", "packages", "overlays"}:
        purpose = "production"
        reason = "production Nix definition"
    return {"purpose": purpose, "material": material,
            "component": parts[0] if len(parts) > 1 else "root",
            "classification_reason": f"{POLICY_VERSION}: {reason}; material={material}"}


def _run(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(args, cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                           env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
        return r.stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_state(root: Path) -> tuple[str | None, bool | None, str | None]:
    revision = _run(root, "git", "rev-parse", "HEAD")
    status = _run(root, "git", "status", "--porcelain=v1", "--untracked-files=all")
    fingerprint = hashlib.sha256(status.encode()).hexdigest() if status is not None else None
    return (revision.strip() if revision else None, bool(status.strip()) if status is not None else None, fingerprint)


def verify_capture(repo: Path, inventory: CapturedInventory) -> None:
    """Raise if Git state, source bytes, or modes changed since capture."""
    source = Path(repo).resolve()
    if _git_state(source) != (inventory.revision, inventory.dirty, inventory.status_fingerprint):
        raise RuntimeError("repository Git state changed after Chisel capture")
    for record in inventory.files:
        if not record.included or record.source_kind not in {"file", "internal_symlink_copy"}:
            continue
        original = source / record.path
        if not original.resolve().is_relative_to(source):
            raise RuntimeError(f"captured source path now escapes repository: {record.path}")
        try:
            if record.source_kind == "internal_symlink_copy":
                if not original.is_symlink() or os.readlink(original) != record.symlink_target:
                    raise RuntimeError(f"source symlink changed after Chisel capture: {record.path}")
                actual_path = (original.parent / str(record.symlink_target)).resolve()
            else:
                if original.is_symlink() or not original.is_file():
                    raise RuntimeError(f"source file changed type after Chisel capture: {record.path}")
                actual_path = original
            if not actual_path.resolve().is_relative_to(source):
                raise RuntimeError(f"captured source target now escapes repository: {record.path}")
            if hashlib.sha256(actual_path.read_bytes()).hexdigest() != record.sha256:
                raise RuntimeError(f"source content changed after Chisel capture: {record.path}")
            if stat.S_IMODE(actual_path.stat().st_mode) != record.mode:
                raise RuntimeError(f"source mode changed after Chisel capture: {record.path}")
        except OSError as exc:
            raise RuntimeError(f"source unavailable after Chisel capture: {record.path}: {exc}") from exc


def _patterns(plan: Any, default_ignore: tuple[str, ...]) -> tuple[str, ...]:
    # *.lock was historically excluded, but lockfiles are dependency evidence.
    return tuple(p for p in (*default_ignore, *getattr(plan, "extra_ignore", ())) if p != "*.lock")


def capture_inventory(
    plan: Any,
    destination: Path,
    *,
    default_ignore: tuple[str, ...] = (),
    scratchpad_include: tuple[str, ...] = (),
    accelerant_include: tuple[str, ...] = (),
    accelerant_ignore: tuple[str, ...] = (),
    committed_paths: tuple[str, ...] | None = None,
    committed_revision: str | None = None,
) -> CapturedInventory:
    """Capture source files once and record slice membership and provenance.

    The inventory includes tracked and git-nonignored files, plus explicitly
    selected ignored paths. It copies only regular files and records symlinks
    without following them. A changing file or git state aborts the capture.
    """
    source = Path(plan.path).resolve()
    dest = Path(destination)
    root = dest / "source"
    root.mkdir(parents=True, exist_ok=True)
    before_state = (committed_revision, False, None) if committed_paths is not None else _git_state(source)
    listed = "\0".join(committed_paths) if committed_paths is not None else _run(source, "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if listed is None:
        raise RuntimeError(f"cannot enumerate Chisel inventory with git ls-files: {source}")
    visible_candidates = {x for x in listed.split("\0") if x and not x.startswith("../")}
    ignored_agent_candidates: set[str] = set()
    # Repomix disables gitignore only for slices that explicitly include .agent.
    # Keep that opt-in local to those slices instead of letting their paths leak
    # into generic slices through the shared candidate union.
    explicit_agent_patterns = [
        pattern
        for s in getattr(plan, "slices", ())
        for pattern in getattr(s, "include", ())
        if pattern.removeprefix("./").startswith(".agent/")
    ]
    special_agent_patterns = [
        pattern
        for pattern in (*scratchpad_include, *accelerant_include)
        if pattern.removeprefix("./").startswith(".agent/")
    ]
    all_patterns = (*explicit_agent_patterns, *special_agent_patterns)
    agent_root = source / ".agent"
    if all_patterns and agent_root.is_dir():
        for base, dirs, names in os.walk(agent_root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in {".git", "cache", "caches", "target", "node_modules", "__pycache__", "tmp"}]
            for name in names:
                rel = (Path(base) / name).relative_to(source).as_posix()
                if _matches(rel, all_patterns):
                    ignored_agent_candidates.add(rel)
    candidates = visible_candidates | ignored_agent_candidates
    excludes = _patterns(plan, default_ignore)
    slices: dict[str, tuple[str, ...]] = {}
    for s in getattr(plan, "slices", ()):
        member = []
        ignored = (*excludes, *getattr(s, "extra_ignore", ()))
        slice_includes = tuple(getattr(s, "include", ()))
        permit_ignored_agent = any(pattern.removeprefix("./").startswith(".agent/") for pattern in slice_includes)
        slice_candidates = visible_candidates | (ignored_agent_candidates if permit_ignored_agent else set())
        for path in slice_candidates:
            if _matches(path, slice_includes) and not _matches(path, ignored):
                member.append(path)
        slices[str(s.name)] = tuple(sorted(member))
    membership: dict[str, list[str]] = {}
    special_context: dict[str, list[str]] = {}
    for slice_name, paths in slices.items():
        for path in paths:
            membership.setdefault(path, []).append(f"slice:{slice_name}")
    for path in candidates:
        if _matches(path, scratchpad_include):
            membership.setdefault(path, []).append("scratchpad")
            special_context.setdefault(path, []).append("scratchpad")
        if _matches(path, accelerant_include) and not _matches(path, accelerant_ignore):
            membership.setdefault(path, []).append("accelerants")
            special_context.setdefault(path, []).append("accelerants")
    # Copy the entire visible source tree, including docs/manifests/context that
    # were never assigned to an XML slice. Explicit memberships remain separate.
    records: list[InventoryFile] = []
    for rel in sorted(candidates):
        src = source / rel
        included_by = tuple(sorted(set(membership.get(rel, ()))))
        excluded_by: list[str] = []
        explicit_context = rel in special_context
        if _matches(rel, excludes) and not explicit_context:
            excluded_by.append("default_or_project_ignore")
        # Lack of an XML membership is not a package exclusion: the captured
        # source tree is the raw browsing representation for the attachment.
        try:
            role, reason = classify_role(rel, project=str(getattr(plan, "name", "")))
            if excluded_by:
                info = src.lstat()
                target = os.readlink(src) if stat.S_ISLNK(info.st_mode) else None
                kind = "excluded_symlink" if target is not None else "file"
                records.append(InventoryFile(rel, None, info.st_size, role, reason, included_by, tuple(excluded_by), kind, False, target, stat.S_IMODE(info.st_mode)))
                continue
            if src.is_symlink():
                target = os.readlink(src)
                resolved = (src.parent / target).resolve()
                if not resolved.is_relative_to(source):
                    records.append(InventoryFile(rel, None, None, "context", "symlink provenance", included_by, tuple(excluded_by), "escaping_symlink", False, target, None))
                    continue
                # Preserve link metadata in inventory; materialize its in-repo
                # target under the link path only when the resolved target is a file.
                if not resolved.is_file():
                    records.append(InventoryFile(rel, None, None, "context", "symlink provenance", included_by, tuple(excluded_by), "symlink", False, target, None))
                    continue
                src = resolved
                source_kind, symlink_target = "internal_symlink_copy", target
            elif src.is_file():
                source_kind, symlink_target = "file", None
            else:
                records.append(InventoryFile(rel, None, None, "unclassified", "not a regular file", included_by, tuple(excluded_by), "unsupported", False, None, None))
                continue
            data = src.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            mode = stat.S_IMODE(src.stat().st_mode)
            target_path = root / rel
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(data)
            os.chmod(target_path, mode)
            if hashlib.sha256(target_path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"captured copy hash mismatch: {rel}")
            records.append(InventoryFile(rel, digest, len(data), role, reason, included_by, tuple(excluded_by), source_kind, True, symlink_target, mode))
        except OSError as exc:
            records.append(InventoryFile(rel, None, None, "unclassified", f"unreadable: {type(exc).__name__}", included_by, tuple((*excluded_by, "unreadable")), "unreadable", False, None, None))
    after_state = before_state if committed_paths is not None else _git_state(source)
    if before_state != after_state:
        raise RuntimeError(f"repository changed during Chisel capture: {before_state!r} -> {after_state!r}")
    # Membership lists contain only files actually materialized in the captured
    # tree. Failed reads and unsafe links remain explicit inventory gaps.
    capturable = {r.path for r in records if r.included and r.source_kind in {"file", "internal_symlink_copy"}}
    slices = {name: tuple(path for path in paths if path in capturable) for name, paths in slices.items()}
    valid_memberships = {path: labels for path, labels in membership.items() if path in capturable}
    records = [replace(r, included_by=tuple(sorted(set(valid_memberships.get(r.path, ()))))) for r in records]
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    project = str(getattr(plan, "name", ""))
    provisional = CapturedInventory(root, tuple(records), slices, project, generated_at, before_state[0], before_state[1], POLICY_VERSION, "", before_state[2])
    if committed_paths is None:
        verify_capture(source, provisional)
    identity = {
        "revision": before_state[0],
        "policy_version": POLICY_VERSION,
        "files": [
            {
                "path": r.path,
                "sha256": r.sha256,
                "size_bytes": r.size_bytes,
                "role": r.role,
                "included": r.included,
                "included_by": r.included_by,
                "excluded_by": r.excluded_by,
                "mode": r.mode,
                "source_kind": r.source_kind,
            }
            for r in records
        ],
    }
    records = [replace(r, **classify_dimensions(r.path, role=r.role, binary=(b"\0" in (root / r.path).read_bytes()[:8192]) if r.included else False)) for r in records]
    identity["files"] = [asdict(r) for r in records]
    snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    inv = CapturedInventory(root, tuple(records), slices, project, generated_at, before_state[0], before_state[1], POLICY_VERSION, snapshot_id, before_state[2])
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / "inventory.jsonl").open("w", encoding="utf-8") as f:
        for record in inv.files:
            f.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    capture = {"project": inv.project, "generated_at": inv.generated_at, "revision": inv.revision, "dirty": inv.dirty, "status_fingerprint": inv.status_fingerprint, "policy_version": inv.policy_version, "snapshot_id": inv.snapshot_id, "candidate_method": "pinned Git objects via ls-tree/cat-file; no checkout" if committed_paths is not None else "git ls-files --cached --others --exclude-standard -z plus declared ignored .agent patterns", "metric_ignore_files": [r.path for r in records if Path(r.path).name in {".ignore", ".tokeignore"} and r.included], "memberships": {k: list(v) for k, v in slices.items()}}
    (dest / "capture.json").write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return inv
