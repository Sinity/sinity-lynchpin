from __future__ import annotations

from typing import Any

from ..core.projects import canonical_project_name
from .analysis_artifact_helpers import dict_or_empty, list_or_empty, string_tuple
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim


def _active_project_snapshot_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return ()
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        exists = bool(row.get("exists", True))
        is_git_repo = bool(row.get("is_git_repo", True))
        if not exists or not is_git_repo:
            reason = "missing local checkout" if not exists else "not a git repository"
            claims.append(
                AnalysisClaim(
                    id=f"active-project-snapshot:{project}",
                    artifact_name=artifact.name,
                    claim_type="project_snapshot_unavailable",
                    project=project,
                    summary=f"{project}: active registry entry unavailable for code snapshot ({reason})",
                    payload={
                        "window": window,
                        "path": row.get("path"),
                        "exists": exists,
                        "is_git_repo": is_git_repo,
                    },
                    confidence=0.9,
                    generated_at=artifact.generated_at,
                )
            )
            continue
        structure = dict_or_empty(row.get("structure"))
        recent = dict_or_empty(row.get("recent_git"))
        quality_gates = string_tuple(row.get("quality_gates"))
        commit_count = int(recent.get("commit_count") or 0)
        active_days = int(recent.get("active_days") or 0)
        counted_lines = int(structure.get("counted_lines") or 0)
        counted_files = int(structure.get("counted_files") or 0)
        gate_text = (
            ", ".join(quality_gates[:6]) if quality_gates else "no detected gates"
        )
        summary = (
            f"{project}: {commit_count} first-parent commits across {active_days} active days; "
            f"{counted_files} tracked text files / {counted_lines} lines; gates: {gate_text}"
        )
        claims.append(
            AnalysisClaim(
                id=f"active-project-snapshot:{project}",
                artifact_name=artifact.name,
                claim_type="project_snapshot",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "default_branch": row.get("default_branch"),
                    "head": row.get("head"),
                    "dirty": row.get("dirty"),
                    "structure": structure,
                    "quality_gates": quality_gates,
                    "recent_git": {
                        "commit_count": commit_count,
                        "active_days": active_days,
                        "files_changed": recent.get("files_changed"),
                        "category_touches": recent.get("category_touches"),
                        "capped_category_touches": recent.get(
                            "capped_category_touches"
                        ),
                        "conventional_kinds": recent.get("conventional_kinds"),
                        "large_touch_commits": recent.get("large_touch_commits"),
                        "top_subjects": recent.get("top_subjects"),
                    },
                },
                confidence=0.82,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _active_work_package_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return ()
    packages_by_id = _work_packages_by_id(projects)
    top_ids = _balanced_work_package_ids(projects, payload)
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        package_count = int(row.get("package_count") or 0)
        commit_count = int(row.get("commit_count") or 0)
        packages = list_or_empty(row.get("packages"))
        top_project_packages = sorted(
            (pkg for pkg in packages if isinstance(pkg, dict)),
            key=lambda pkg: -float(pkg.get("durability_adjusted_scope") or 0),
        )[:5]
        summary = f"{project}: {package_count} active work packages over {commit_count} landed commits"
        claims.append(
            AnalysisClaim(
                id=f"active-work-packages:{project}",
                artifact_name=artifact.name,
                claim_type="work_package_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "package_count": package_count,
                    "commit_count": commit_count,
                    "top_work_packages": [
                        {
                            "work_package_id": pkg.get("work_package_id"),
                            "label": pkg.get("label"),
                            "unit_type": pkg.get("unit_type"),
                            "commit_count": pkg.get("commit_count"),
                            "durability_adjusted_scope": pkg.get(
                                "durability_adjusted_scope"
                            ),
                            "refs": pkg.get("refs"),
                        }
                        for pkg in top_project_packages
                    ],
                },
                confidence=0.76,
                generated_at=artifact.generated_at,
            )
        )
    for package_id in top_ids[:24]:
        package = packages_by_id.get(package_id)
        if package is None:
            continue
        project = canonical_project_name(package.get("project"))
        if project is None or (selected and project not in selected):
            continue
        claims.append(
            AnalysisClaim(
                id=f"active-work-package:{package_id}",
                artifact_name=artifact.name,
                claim_type="work_package",
                project=project,
                summary=(
                    f"{project}: {package.get('label')} "
                    f"({package.get('unit_type')}, {package.get('commit_count')} commits)"
                ),
                payload={
                    "window": window,
                    "work_package_id": package_id,
                    "unit_type": package.get("unit_type"),
                    "unit_key": package.get("unit_key"),
                    "label": package.get("label"),
                    "status": package.get("status"),
                    "lifecycle": package.get("lifecycle"),
                    "confidence": package.get("confidence"),
                    "first_date": package.get("first_date"),
                    "last_date": package.get("last_date"),
                    "commit_count": package.get("commit_count"),
                    "commit_shas": package.get("commit_shas"),
                    "top_surfaces": package.get("top_surfaces"),
                    "scope_geom": package.get("scope_geom"),
                    "durability_adjusted_scope": package.get(
                        "durability_adjusted_scope"
                    ),
                    "refs": package.get("refs"),
                    "caveats": package.get("caveats"),
                },
                confidence=float(package.get("confidence") or 0.65),
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _balanced_work_package_ids(
    projects: list[Any], payload: dict[str, Any]
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for row in projects:
        if not isinstance(row, dict):
            continue
        packages = sorted(
            (
                pkg
                for pkg in list_or_empty(row.get("packages"))
                if isinstance(pkg, dict)
            ),
            key=_work_package_sort_key,
        )
        for package in packages[:3]:
            package_id = package.get("work_package_id")
            if isinstance(package_id, str) and package_id not in seen:
                selected.append(package_id)
                seen.add(package_id)
    global_ids = [
        str(row.get("work_package_id"))
        for row in list_or_empty(
            dict_or_empty(payload.get("summary")).get("top_work_packages")
        )
        if isinstance(row, dict) and row.get("work_package_id")
    ]
    for package_id in global_ids:
        if package_id not in seen:
            selected.append(package_id)
            seen.add(package_id)
        if len(selected) >= 30:
            break
    return selected[:30]


def _work_package_sort_key(package: dict[str, Any]) -> tuple[float, float, str]:
    scope = float(package.get("durability_adjusted_scope") or 0.0)
    commits = float(package.get("commit_count") or 0.0)
    return (-scope, -commits, str(package.get("work_package_id") or ""))


def _work_packages_by_id(projects: list[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in projects:
        if not isinstance(row, dict):
            continue
        for package in list_or_empty(row.get("packages")):
            if not isinstance(package, dict):
                continue
            package_id = package.get("work_package_id")
            if isinstance(package_id, str):
                result[package_id] = package
    return result


def _project_velocity_window_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return ()
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        micro = dict_or_empty(row.get("micro_effort"))
        meso = dict_or_empty(row.get("meso_delivery"))
        support = dict_or_empty(row.get("cross_source_support"))
        interpretation = dict_or_empty(row.get("interpretation_signals"))
        support_level = str(interpretation.get("support_level") or "weak")
        summary = (
            f"{project}: {support_level} velocity-window support; "
            f"{int(micro.get('commit_count') or 0)} commits, "
            f"{int(meso.get('landed_package_count') or 0)} packages, "
            f"{int(support.get('cross_source_days') or 0)} cross-source days"
        )
        claims.append(
            AnalysisClaim(
                id=f"project-velocity-window:{project}:{window.get('start')}:{window.get('end')}",
                artifact_name=artifact.name,
                claim_type="project_velocity_window",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "micro_effort": micro,
                    "meso_delivery": {
                        "landed_package_count": meso.get("landed_package_count"),
                        "github_thread_package_count": meso.get(
                            "github_thread_package_count"
                        ),
                        "heuristic_package_count": meso.get("heuristic_package_count"),
                        "total_durability_adjusted_scope": meso.get(
                            "total_durability_adjusted_scope"
                        ),
                        "top_packages": meso.get("top_packages"),
                    },
                    "cross_source_support": support,
                    "interpretation_signals": interpretation,
                    "caveats": row.get("caveats"),
                },
                confidence=_velocity_confidence(support_level),
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _velocity_confidence(support_level: str) -> float:
    return {"strong": 0.82, "moderate": 0.68, "weak": 0.52}.get(support_level, 0.58)


def _active_github_frontier_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return ()
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        if row.get("status") != "available":
            claims.append(
                AnalysisClaim(
                    id=f"github-frontier:{project}",
                    artifact_name=artifact.name,
                    claim_type="github_frontier_unavailable",
                    project=project,
                    summary=f"{project}: GitHub frontier unavailable ({', '.join(row.get('caveats', []))})",
                    payload={"window": window, "caveats": row.get("caveats")},
                    confidence=0.85,
                    generated_at=artifact.generated_at,
                )
            )
            continue
        open_count = int(row.get("open_item_count") or 0)
        closed_count = int(row.get("recently_closed_item_count") or 0)
        lifecycle = dict_or_empty(row.get("lifecycle_summary"))
        inactivity = dict_or_empty(row.get("open_inactivity_summary"))
        open_frontier = list_or_empty(row.get("open_frontier_items"))
        inactivity_str = (
            "; inactivity: "
            + ", ".join(f"{k}={v}" for k, v in sorted(inactivity.items()))
            if inactivity
            else ""
        )
        summary = (
            f"{project}: {open_count} open items, {closed_count} recently closed; "
            f"lifecycle: {', '.join(f'{k}={v}' for k, v in sorted(lifecycle.items())[:5])}"
            f"{inactivity_str}"
        )
        claims.append(
            AnalysisClaim(
                id=f"github-frontier-summary:{project}",
                artifact_name=artifact.name,
                claim_type="github_frontier_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "open_item_count": open_count,
                    "recently_closed_item_count": closed_count,
                    "lifecycle_summary": lifecycle,
                    "open_inactivity_summary": inactivity,
                    "top_open_frontier": [
                        {
                            "kind": item.get("kind"),
                            "number": item.get("number"),
                            "title": item.get("title"),
                            "labels": item.get("labels"),
                            "linked_packages": item.get("linked_packages"),
                            "inactivity_days": item.get("inactivity_days"),
                            "inactivity_bucket": item.get("inactivity_bucket"),
                        }
                        for item in open_frontier[:8]
                    ],
                },
                confidence=0.74,
                generated_at=artifact.generated_at,
            )
        )
        for item in open_frontier[:5]:
            item_number = item.get("number")
            item_title = item.get("title")
            item_lifecycle = item.get("lifecycle", "open_frontier")
            linked = list_or_empty(item.get("linked_packages"))
            claims.append(
                AnalysisClaim(
                    id=f"github-frontier-item:{project}:{item_number}",
                    artifact_name=artifact.name,
                    claim_type="github_frontier_item",
                    project=project,
                    summary=f"{project}#{item_number}: {item_title} ({item_lifecycle})",
                    payload={
                        "window": window,
                        "kind": item.get("kind"),
                        "number": item_number,
                        "title": item_title,
                        "state": item.get("state"),
                        "url": item.get("url"),
                        "labels": item.get("labels"),
                        "lifecycle": item_lifecycle,
                        "linked_packages": linked,
                        "caveats": item.get("caveats"),
                    },
                    confidence=0.68,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)
