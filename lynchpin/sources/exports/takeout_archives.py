from __future__ import annotations

import io
import tarfile
from pathlib import Path


class TarReader:
    def __init__(self, tar_path: Path):
        self.tar_path = tar_path
        self._tf: tarfile.TarFile | None = None
        self._members: dict[str, tarfile.TarInfo] = {}

    def __enter__(self) -> "TarReader":
        self._tf = tarfile.open(self.tar_path)
        self._members = {member.name: member for member in self._tf.getmembers()}
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        if self._tf is not None:
            self._tf.close()
        self._tf = None
        self._members = {}

    def open(self, member_path: str) -> io.BufferedReader | None:
        if self._tf is None:
            raise RuntimeError("TarReader not opened (use as a context manager).")
        member = self._members.get(member_path)
        if member is None:
            return None
        return self._tf.extractfile(member)

    def read_text(self, member_path: str) -> str | None:
        fh = self.open(member_path)
        if fh is None:
            return None
        return fh.read().decode("utf-8", errors="replace")

    def iter_members(self) -> list[tarfile.TarInfo]:
        return list(self._members.values())

    def has_member(self, member_path: str) -> bool:
        return member_path in self._members

    def member_size(self, member_path: str) -> int | None:
        member = self._members.get(member_path)
        return member.size if member is not None else None


def expand_takeout_parts(path: Path) -> list[Path]:
    """Expand a `...-001.tgz` seed path into all sibling takeout parts."""
    if not path.exists():
        return []
    name = path.name
    if name.endswith(".tgz"):
        stem = name[:-4]
        prefix, _, part = stem.rpartition("-")
        if prefix and part.isdigit() and len(part) == 3:
            return [candidate for candidate in sorted(path.parent.glob(f"{prefix}-*.tgz")) if candidate.exists()]
    return [path]


def discover_seed_archives(root: Path) -> list[Path]:
    if not root.exists():
        return []
    seeds = sorted(root.glob("takeout*-001.tgz"))
    if seeds:
        return seeds
    return sorted(root.glob("takeout*.tgz"))


def resolve_archives(*, explicit_seeds: list[Path], root: Path) -> list[Path]:
    expanded_takeouts: list[Path] = []
    for seed in explicit_seeds or discover_seed_archives(root):
        expanded_takeouts.extend(expand_takeout_parts(seed))

    seen_takeouts: set[str] = set()
    takeout_paths: list[Path] = []
    for path in sorted(expanded_takeouts, key=lambda candidate: candidate.name):
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen_takeouts:
            continue
        seen_takeouts.add(key)
        takeout_paths.append(path)
    return takeout_paths


def select_archive_with_member(takeouts: list[TarReader], member_path: str) -> TarReader | None:
    matching = [tar for tar in takeouts if tar.has_member(member_path)]
    if not matching:
        return None
    return max(matching, key=lambda tar: tar.member_size(member_path) or 0)
