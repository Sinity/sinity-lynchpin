from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class CommitInfo:
    sha: str
    summary: str
    author: str
    authored_at: datetime


class GitRepository:
    """Subprocess-backed Git helper for consistent access patterns."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def recent_commits(self, max_count: int = 20) -> List[CommitInfo]:
        fmt = "%H%x1f%an%x1f%aI%x1f%s"
        output = self._git("log", f"-n{max_count}", f"--pretty={fmt}")
        commits: List[CommitInfo] = []
        if not output:
            return commits
        for line in output.splitlines():
            sha, author, authored_at, subject = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
            try:
                authored_dt = datetime.fromisoformat(authored_at)
            except ValueError:
                authored_dt = datetime.min
            commits.append(
                CommitInfo(sha=sha, summary=subject, author=author, authored_at=authored_dt)
            )
        return commits

    def list_tree(self, treeish: str = "HEAD", path: Optional[str] = None) -> List[str]:
        args = ["ls-tree", "--name-only", treeish]
        if path:
            args.append(path)
        output = self._git(*args)
        return output.splitlines() if output else []

    def read_file(self, path: str, treeish: str = "HEAD") -> Optional[str]:
        output = self._git("show", f"{treeish}:{path}")
        return output or None

    def diff(self, treeish_a: str, treeish_b: str, paths: Optional[Iterable[str]] = None) -> str:
        args = ["diff", treeish_a, treeish_b]
        if paths:
            args.extend(paths)
        return self._git(*args)
