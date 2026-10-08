"""Source path and file classification helpers for code snapshots."""

from __future__ import annotations

from pathlib import Path
import os
from .chisel import REPO_PLANS  # noqa: F401


def code_snapshots_path(project: str | None = None) -> Path:
    """Return the stable output root (or per-project subdir) for code snapshots."""
    from lynchpin.core.config import get_config

    base = Path(os.environ.get("LYNCHPIN_PROJECTS_ROOT", get_config().data_root / "projects"))
    if project is not None and (not project or Path(project).name != project or project in {".", ".."}):
        raise ValueError("project must be a single path component")
    return base / project / "snapshots/current" if project else base / "shared/snapshots"


def code_snapshot_export_path(project: str) -> Path:
    return code_snapshots_path(project).parent / "exports" / f"{project}-all.tar.gz"


def snapshot_layout_plan(source: Path) -> list[dict[str, str]]:
    """Describe same-filesystem moves without opening or regenerating packages.

    Project trees and attachment archives remain intact. Shared indexes and
    indivisible portfolio/history packages stay together in the shared home.
    The external cutover journal executes these owner-declared moves.
    """
    if not source.is_dir() or source.is_symlink():
        raise ValueError("snapshot source must be a native directory")
    moves = []
    for name in sorted(REPO_PLANS):
        project = source / name
        if project.exists():
            if not project.is_dir() or project.is_symlink():
                raise ValueError(f"snapshot project is not a native directory: {project}")
            moves.append({"source": str(project), "destination": str(code_snapshots_path(name)), "owner": "chisel"})
        archive = source / f"{name}-all.tar.gz"
        if archive.exists():
            if not archive.is_file() or archive.is_symlink():
                raise ValueError(f"snapshot attachment is not a native file: {archive}")
            moves.append({"source": str(archive), "destination": str(code_snapshot_export_path(name)), "owner": "chisel"})
    moves.append({"source": str(source), "destination": str(code_snapshots_path()), "owner": "chisel"})
    return moves


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
