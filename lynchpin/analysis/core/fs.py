"""Filesystem helpers shared by legacy analysis commands."""

from __future__ import annotations

import os
from collections.abc import Iterator
from os import PathLike


def count_lines(filepath: str | PathLike[str]) -> int:
    """Safely count lines in a file."""
    try:
        with open(filepath, "r", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def walk_files(
    base_dir: str | PathLike[str],
    skip_dirs: set[str] | None = None,
    target_exts: set[str] | None = None,
    exclude_exts: set[str] | None = None,
) -> Iterator[tuple[str, list[str], str, str, str]]:
    """
    Yields (root, dir, filename, filepath, relative_path)
    """
    if skip_dirs is None:
        skip_dirs = {
            ".git",
            "venv",
            ".venv",
            "node_modules",
            "__pycache__",
            "target",
            ".lynchpin",
            "artefacts",
            ".direnv",
            ".ruff_cache",
            ".pytest_cache",
            ".mypy_cache",
            "dist",
            "build",
            ".eggs",
            "result",
            ".claude",
            ".playwright-mcp",
            ".sinex",
            ".cargo",
            ".nats",
            ".cache",
            ".local",
        }

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if target_exts and ext not in target_exts:
                continue
            if exclude_exts and ext in exclude_exts:
                continue

            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, base_dir)
            yield root, dirs, f, fp, rel
