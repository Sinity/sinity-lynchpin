"""Source path and file classification helpers for code snapshots."""

from __future__ import annotations

from pathlib import Path
from .chisel import REPO_PLANS  # noqa: F401


def code_snapshots_path(project: str | None = None) -> Path:
    """Return the stable output root (or per-project subdir) for code snapshots."""
    from lynchpin.core.config import get_config

    base = get_config().data_root / "library/code"
    return base / project if project else base


def _classify_slice_kind(filename: str, project: str) -> str:
    """Classify a chisel output file into a named kind."""
    for prefix, kind in (
        ("source/", "captured_source"), ("structure/", "structure_evidence"),
        ("history/", "history_evidence"), ("metrics/", "source_metrics"),
        ("trackers/", "tracker_evidence"), ("verification/", "verification_evidence"),
        ("representations/", "representation_manifest"),
    ):
        if filename.startswith(prefix):
            return kind
    if filename in {"capture.json", "inventory.jsonl"}:
        return "source_inventory"
    if filename in {"browse.py", "index.sqlite3", "START_HERE.md"}:
        return "offline_navigation"
    if filename == f"{project}-all.tar.gz":
        return "combined_tar"
    if filename.endswith("-working-tree.tar.gz"):
        return "working_tree_tar"
    if filename.endswith(".bundle"):
        return "git_bundle"
    if filename.endswith("-repo-tree.txt"):
        return "repo_tree"
    if filename.endswith(("-git-log.xml", "-git-log-all-refs.xml")):
        return "xml_git_log"
    if "-issues-" in filename and filename.endswith(".xml"):
        return "xml_issues"
    if "-prs-" in filename and filename.endswith(".xml"):
        return "xml_prs"
    if filename.endswith((".xml.gz", "-compressed.xml")):
        return "xml_compressed"
    if filename.endswith(".xml"):
        return "xml_slice"
    return "other"
