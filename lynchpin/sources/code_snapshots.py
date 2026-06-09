"""Path helpers, file classification, and bundle-build re-exports for code snapshots.

This module provides a sources-layer entry point for the chisel bundle builder so
that ``ingest/`` modules can import it without crossing into the ``analysis/``
layer.  The builder implementation lives in ``sources.chisel``; re-exported here
so callers have a single import path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Re-export chisel symbols so ingest/ can import from sources/ only.
from .chisel import (  # noqa: F401
    REPO_PLANS,
    build_chisel_bundles,
)


def build_code_snapshot_bundles(output_root: Path) -> dict[str, Any]:
    """Run the chisel bundle builder and return its result dict unchanged.

    A thin wrapper so ingest modules have a sources-layer call target.
    """
    return build_chisel_bundles(output_root=output_root)


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
