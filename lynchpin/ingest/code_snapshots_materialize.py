"""Materializer for the code_snapshots substrate product.

Calls build_chisel_bundles() to generate repomix XML slices, git bundles,
working-tree tars, and issue XML per project, then promotes the per-project
run metadata and per-file slice index into the DuckDB substrate.

Raises MaterializationError only if ALL projects fail. Partial success
(some projects OK, some failed) is non-fatal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest._manifest import write_manifest


def code_snapshots_stale() -> bool:
    """True if any REPO_PLANS repo's .git/HEAD mtime is newer than last promotion."""
    from lynchpin.sources.code_snapshots import REPO_PLANS
    from lynchpin.substrate.connection import connect

    try:
        with connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT project, MAX(run_at) FROM code_snapshot_run"
                " WHERE refresh_id = 'latest' GROUP BY project"
            ).fetchall()
    except Exception:
        return True

    if not rows:
        return True

    latest = {r[0]: r[1] for r in rows}
    for plan in REPO_PLANS.values():
        head = plan.path / ".git" / "HEAD"
        if not head.exists():
            continue
        project_run_at = latest.get(plan.name)
        if project_run_at is None:
            return True
        if project_run_at.tzinfo is None:
            project_run_at = project_run_at.replace(tzinfo=timezone.utc)
        if head.stat().st_mtime > project_run_at.timestamp():
            return True
    return False


def iter_code_snapshots(project: str | None = None):
    """Yield code_snapshot_run dicts from the substrate for the given project."""
    from lynchpin.substrate.code_snapshots import iter_code_snapshot_runs
    from lynchpin.substrate.connection import connect

    with connect(read_only=True) as conn:
        yield from iter_code_snapshot_runs(conn, project=project)


def materialize_code_snapshots() -> dict[str, Any]:
    """Run chisel → promote rows → return manifest dict.

    Output goes to the stable path returned by code_snapshots_path() so
    repeated runs overwrite rather than accumulate timestamped directories.
    """
    from lynchpin.sources.code_snapshots import build_chisel_bundles, code_snapshots_path
    from lynchpin.substrate.code_snapshots import (
        promote_code_snapshot_runs,
        promote_code_snapshot_slices,
    )
    from lynchpin.substrate.connection import connect, update_read_snapshot

    output_root = code_snapshots_path()
    output_root.mkdir(parents=True, exist_ok=True)

    run_at = datetime.now(timezone.utc)
    bundle_result = build_chisel_bundles(output_root=output_root)

    run_rows, slice_rows = _results_to_rows(bundle_result, run_at, output_root)

    all_failed = all(
        r.get("status") == "failed"
        for r in bundle_result.get("projects", {}).values()
    )
    if all_failed and bundle_result.get("projects"):
        first_err = next(
            (r.get("error") or r.get("errors") for r in bundle_result["projects"].values()),
            "all projects failed",
        )
        raise MaterializationError("code_snapshots", reason=str(first_err))

    with connect(recover_corrupt_from_snapshot=True) as conn:
        n_runs = promote_code_snapshot_runs(conn, rows=run_rows)
        n_slices = promote_code_snapshot_slices(conn, rows=slice_rows)

    update_read_snapshot()
    manifest = {
        "dataset": "code_snapshots",
        "row_count": n_runs + n_slices,
        "run_count": n_runs,
        "slice_count": n_slices,
        "materialized_path": str(output_root),
    }
    manifest_path = output_root / "code_snapshots.manifest.json"
    write_manifest(manifest_path, manifest)
    return manifest


def _results_to_rows(
    bundle_result: dict[str, Any],
    run_at: datetime,
    output_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert build_chisel_bundles() output to (run_rows, slice_rows)."""
    from lynchpin.sources.code_snapshots import _classify_slice_kind

    run_rows: list[dict[str, Any]] = []
    slice_rows: list[dict[str, Any]] = []

    for project_name, r in bundle_result.get("projects", {}).items():
        status = r.get("status", "failed")
        git = r.get("git") or {}
        errors = r.get("errors")
        error_str: str | None = None
        if isinstance(errors, list):
            error_str = "; ".join(errors) if errors else None
        elif isinstance(errors, str):
            error_str = errors or None
        elif r.get("error"):
            error_str = str(r["error"])

        out_dir = output_root / project_name

        run_rows.append({
            "project": project_name,
            "run_at": run_at,
            "git_commit": git.get("commit", ""),
            "git_branch": git.get("branch", ""),
            "git_dirty": bool(git.get("dirty", False)),
            "issues_open": r.get("issues_open"),
            "issues_closed": r.get("issues_closed"),
            "gitlog_commits": r.get("gitlog_commits"),
            "xml_valid": bool(r.get("xml_valid", True)),
            "elapsed_s": r.get("elapsed_s"),
            "status": status,
            "errors": error_str,
            "output_dir": str(out_dir),
            "total_bytes": r.get("total_bytes", 0),
        })

        if status == "failed" or not out_dir.exists():
            continue

        # Enumerate all files in the per-project dir
        for f in sorted(out_dir.iterdir()):
            if not f.is_file():
                continue
            slice_rows.append({
                "project": project_name,
                "filename": f.name,
                "kind": _classify_slice_kind(f.name, project_name),
                "size_bytes": f.stat().st_size,
                "path": str(f),
            })

        # Combined tar lives at output_root level, not inside the project dir
        combined = output_root / f"{project_name}-all.tar.gz"
        if combined.exists():
            slice_rows.append({
                "project": project_name,
                "filename": combined.name,
                "kind": "combined_tar",
                "size_bytes": combined.stat().st_size,
                "path": str(combined),
            })

    return run_rows, slice_rows
