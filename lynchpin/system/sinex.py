from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..sources.indices.repos import GitRepository


@dataclass
class SinexRepo:
    name: str
    path: Path
    branch: Optional[str]
    head: Optional[str]
    last_commit_at: Optional[datetime]
    latest_commit: Optional[str]


@dataclass
class ConnectorSpec:
    path: Path
    kind: str
    summary: str


def iter_repo_state() -> Iterator[SinexRepo]:
    root = Path("/realm/project/sinex")
    exists = root.exists()
    branch = None
    head_sha = None
    last_commit_at = None
    latest_summary = None
    if exists:
        repo = GitRepository(root)
        commits = repo.recent_commits(1)
        head = commits[0] if commits else None
        head_sha = head.sha if head else None
        last_commit_at = head.authored_at if head else None
        latest_summary = head.summary if head else None
        branch = repo._git("rev-parse", "--abbrev-ref", "HEAD") or None
    yield SinexRepo(
        name="sinex",
        path=root,
        branch=branch,
        head=head_sha,
        last_commit_at=last_commit_at,
        latest_commit=latest_summary,
    )


def iter_connectors(root: Optional[Path] = None) -> Iterator[ConnectorSpec]:
    # nodes/ is the current layout; fall back to legacy satellites/ if absent
    default_root = Path("/realm/project/sinex/crate/nodes")
    legacy_root = Path("/realm/project/sinex/crate/satellites")
    if root:
        base = Path(root)
    elif default_root.exists():
        base = default_root
    elif legacy_root.exists():
        base = legacy_root
    else:
        return iter(())

    def generator() -> Iterator[ConnectorSpec]:
        for node_dir in sorted(base.iterdir()):
            if not node_dir.is_dir():
                continue
            # Use crate-level main lib file for docstring, else first .rs
            candidate = node_dir / "src" / "lib.rs"
            if not candidate.exists():
                candidate = node_dir / "src" / "main.rs"
            if not candidate.exists():
                rs_files = sorted(node_dir.rglob("*.rs"))
                candidate = rs_files[0] if rs_files else None
            if not candidate:
                continue
            summary = _first_docstring(candidate.read_text(encoding="utf-8"))
            yield ConnectorSpec(path=node_dir, kind=node_dir.name, summary=summary)

    return generator()


def _first_docstring(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("///"):
            return stripped.lstrip("///").strip()
    return ""
