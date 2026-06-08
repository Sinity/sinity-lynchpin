"""Path helpers and file classification for code snapshot (chisel/repomix) outputs.

This module contains only pure path utilities and the filename classifier.
Substrate reads and staleness checks live in lynchpin.ingest.code_snapshots_materialize
(which has access to both substrate and analysis layers).
"""

from __future__ import annotations

from pathlib import Path


def code_snapshots_path(project: str | None = None) -> Path:
    """Return the stable output root (or per-project subdir) for code snapshots."""
    from lynchpin.core.config import get_config

    base = get_config().derived_root / "code-snapshots"
    return base / project if project else base


def _classify_slice_kind(filename: str, project: str) -> str:
    """Classify a chisel output file into a named kind."""
    if filename == f"{project}-all.tar.gz":
        return "combined_tar"
    if filename.endswith("-working-tree.tar.gz"):
        return "working_tree_tar"
    if filename.endswith(".bundle"):
        return "git_bundle"
    if filename.endswith("-repo-tree.txt"):
        return "repo_tree"
    if filename.endswith("-git-log.xml"):
        return "xml_git_log"
    if "-issues-" in filename and filename.endswith(".xml"):
        return "xml_issues"
    if "-prs-" in filename and filename.endswith(".xml"):
        return "xml_prs"
    if filename.endswith(".xml.gz"):
        return "xml_compressed"
    if filename.endswith(".xml"):
        return "xml_slice"
    return "other"
