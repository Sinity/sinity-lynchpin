"""Frontier-vs-velocity reconciliation (Arc C.4).

Cross-references ``active_github_frontier.json`` against
``active_work_packages.json`` and emits ``active_frontier_reconciliation.json``
with three diagnostic categories:

- ``tracking_or_horizon_without_landed_packages``: issues classified as
  tracking/horizon (intent-spine, not actionable workload) that have no
  linked landed work package — confirming they're spine, not stalled work.
- ``executed_without_work_package``: issues the lifecycle classifier marked
  as ``executed`` (closed via PR/commit) that nonetheless have no
  work-package linkage — suggests either misclassified executed status or a
  package-clustering miss.
- ``packages_with_orphan_refs``: work packages whose ``refs.issues`` /
  ``refs.prs`` point to issues that don't appear in the frontier inventory
  for the same project (or appear in other projects entirely) — flagging
  cross-repo reference confusion or stale issue numbers.

Outputs are descriptive, not prescriptive: a closed-as-executed issue
without a work package may be a real classification miss OR a real
work-package-clustering miss; the artifact preserves the raw evidence and
lets the reader judge.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any, Sequence

from ..core.io import load_json_object, resolve_analysis_path, save_json


def build_active_frontier_reconciliation(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    frontier_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    frontier_payload = load_json_object(
        frontier_file or resolve_analysis_path("active_github_frontier.json"),
        label="active GitHub frontier",
    )
    work_payload = load_json_object(
        work_packages_file or resolve_analysis_path("active_work_packages.json"),
        label="active work packages",
    )

    selected = set(projects or ())

    frontier_by_project = _index_frontier(frontier_payload, selected=selected)
    packages_by_project = _index_packages(work_payload, selected=selected)

    tracking_without_packages: list[dict[str, Any]] = []
    executed_without_packages: list[dict[str, Any]] = []
    orphan_refs: list[dict[str, Any]] = []

    project_summaries: list[dict[str, Any]] = []

    all_projects = sorted(set(frontier_by_project) | set(packages_by_project))
    for project in all_projects:
        items = frontier_by_project.get(project, [])
        packages = packages_by_project.get(project, [])

        # Set of issue / PR numbers that appear in any package's refs.
        package_issue_refs: set[int] = set()
        package_pr_refs: set[int] = set()
        for package in packages:
            refs = package.get("refs") or {}
            if isinstance(refs, dict):
                package_issue_refs.update(_int_set(refs.get("issues") or ()))
                package_pr_refs.update(_int_set(refs.get("prs") or ()))

        project_tracking = 0
        project_executed = 0
        project_orphans = 0

        for item in items:
            if item.get("kind") != "issue":
                continue
            number = _int_or_none(item.get("number"))
            if number is None:
                continue
            lifecycle = str(item.get("lifecycle") or "")
            linked = item.get("linked_packages") or []
            has_pkg = bool(linked) or number in package_issue_refs

            if lifecycle == "tracking_or_horizon" and not has_pkg:
                tracking_without_packages.append({
                    "project": project,
                    "issue_ref": f"issue#{number}",
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "lifecycle_confidence": item.get("lifecycle_confidence"),
                    "inactivity_bucket": item.get("inactivity_bucket"),
                    "interpretation": "open spine without delivery — confirms intent-tracking status",
                })
                project_tracking += 1
            elif lifecycle == "executed" and not has_pkg:
                executed_without_packages.append({
                    "project": project,
                    "issue_ref": f"issue#{number}",
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "closed_at": item.get("closed_at"),
                    "lifecycle_confidence": item.get("lifecycle_confidence"),
                    "interpretation": "classified executed but no work-package linkage — possible classifier miss or package-clustering miss",
                })
                project_executed += 1

        # Packages whose refs name issues that don't appear in the frontier
        # for this project (cross-repo confusion or stale numbers).
        frontier_issue_numbers = _frontier_numbers(items, kind="issue")
        frontier_pr_numbers = _frontier_numbers(items, kind="pr")
        for package in packages:
            refs = package.get("refs") or {}
            if not isinstance(refs, dict):
                continue
            stray_issue_refs = sorted(_int_set(refs.get("issues") or ()) - frontier_issue_numbers)
            stray_pr_refs = sorted(_int_set(refs.get("prs") or ()) - frontier_pr_numbers)
            if stray_issue_refs or stray_pr_refs:
                orphan_refs.append({
                    "project": project,
                    "work_package_id": package.get("work_package_id"),
                    "label": package.get("label"),
                    "stray_issue_refs": [f"issue#{n}" for n in stray_issue_refs],
                    "stray_pr_refs": [f"pr#{n}" for n in stray_pr_refs],
                    "interpretation": "package references items not in this project's frontier — likely cross-repo or stale",
                })
                project_orphans += 1

        project_summaries.append({
            "project": project,
            "tracking_or_horizon_without_packages": project_tracking,
            "executed_without_packages": project_executed,
            "packages_with_orphan_refs": project_orphans,
            "open_item_count": sum(1 for item in items if item.get("state") == "open"),
            "package_count": len(packages),
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "projects": project_summaries,
        "tracking_or_horizon_without_landed_packages": tracking_without_packages,
        "executed_without_work_package": executed_without_packages,
        "packages_with_orphan_refs": orphan_refs,
        "summary": {
            "tracking_count": len(tracking_without_packages),
            "executed_without_package_count": len(executed_without_packages),
            "orphan_ref_package_count": len(orphan_refs),
        },
        "caveats": [
            "tracking/horizon-without-package is descriptive — many tracking issues are intentionally never linked to a single package",
            "executed-without-package can be a classifier miss OR a clustering miss — read the items and judge per case",
            "orphan refs may be legitimate cross-repo references when the related repo isn't in the active project set",
        ],
    }


def run_active_frontier_reconciliation(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    frontier_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_frontier_reconciliation(
        start=start,
        end=end,
        projects=projects,
        frontier_file=frontier_file,
        work_packages_file=work_packages_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _index_frontier(
    frontier_payload: dict[str, Any],
    *,
    selected: set[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    projects = frontier_payload.get("projects")
    if not isinstance(projects, list):
        return dict(result)
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        # Items live across several keys: open_frontier_items, recently_closed_items,
        # tracking_or_horizon_open_items, items_by_lifecycle.
        # `items_by_lifecycle` carries every classified item; prefer it.
        by_lifecycle = row.get("items_by_lifecycle") or {}
        if isinstance(by_lifecycle, dict):
            for items in by_lifecycle.values():
                for item in items or ():
                    if isinstance(item, dict):
                        result[project].append(item)
    return dict(result)


def _index_packages(
    work_payload: dict[str, Any],
    *,
    selected: set[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    projects = work_payload.get("projects")
    if not isinstance(projects, list):
        return dict(result)
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        for package in row.get("packages") or ():
            if isinstance(package, dict):
                result[project].append(package)
    return dict(result)


def _frontier_numbers(items: Sequence[dict[str, Any]], *, kind: str) -> set[int]:
    out: set[int] = set()
    for item in items:
        if item.get("kind") != kind:
            continue
        number = _int_or_none(item.get("number"))
        if number is not None:
            out.add(number)
    return out


def _int_set(values: Any) -> set[int]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    out: set[int] = set()
    for value in values:
        n = _int_or_none(value)
        if n is not None:
            out.add(n)
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "build_active_frontier_reconciliation",
    "run_active_frontier_reconciliation",
]
