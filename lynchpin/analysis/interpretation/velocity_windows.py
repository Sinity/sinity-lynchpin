"""Project velocity-window materializer over active analysis artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any

from ...graph.evidence_graph import build_base_evidence_graph
from ...graph.work_correlation import CorrelatedWorkDay, work_day_correlations
from ..core.io import load_json_object, resolve_analysis_path, save_json

@dataclass
class _Micro:
    commits: int = 0
    active_days: set[str] = field(default_factory=set)
    files_changed: int = 0
    classified_files_changed: int = 0
    conventional_kinds: Counter[str] = field(default_factory=Counter)
    categories: Counter[str] = field(default_factory=Counter)
    path_roots: Counter[str] = field(default_factory=Counter)
    subjects: list[str] = field(default_factory=list)


@dataclass
class _Meso:
    packages: int = 0
    commits: int = 0
    github_thread_packages: int = 0
    heuristic_packages: int = 0
    single_commit_packages: int = 0
    total_scope_geom: float = 0.0
    total_durability_adjusted_scope: float = 0.0
    unit_types: Counter[str] = field(default_factory=Counter)
    surfaces: Counter[str] = field(default_factory=Counter)
    top_packages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Support:
    active_days: set[str] = field(default_factory=set)
    cross_source_days: set[str] = field(default_factory=set)
    sources: Counter[str] = field(default_factory=Counter)
    source_pairs: Counter[str] = field(default_factory=Counter)
    github_lifecycles: Counter[str] = field(default_factory=Counter)
    ai_sessions: int = 0
    focus_minutes: float = 0.0
    shell_commands: int = 0
    raw_log_count: int = 0
    github_refs: int = 0


def build_project_velocity_windows(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_payload: Mapping[str, Any] | None = None,
    work_payload: Mapping[str, Any] | None = None,
    correlation_rows: Sequence[CorrelatedWorkDay] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build multi-dimensional project velocity windows without scalarizing velocity."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    selected = set(projects or ())
    commit_data = dict(
        commit_payload if commit_payload is not None
        else _load_commit_payload(path=commit_facts_file)
    )
    work_data = dict(
        work_payload if work_payload is not None
        else _load_work_payload(path=work_packages_file)
    )
    rows = tuple(correlation_rows) if correlation_rows is not None else _correlation_rows(
        start=start,
        end=end,
        projects=projects,
    )

    project_meta = _project_meta(commit_data, work_data)
    micro = _micro_by_project(commit_data)
    meso = _meso_by_project(work_data)
    support = _support_by_project(rows)
    names = sorted((set(project_meta) | set(micro) | set(meso) | set(support)) - ({""}))
    if selected:
        names = [name for name in names if name in selected]

    project_rows = [
        _project_row(
            name,
            meta=project_meta.get(name, {}),
            micro=micro.get(name, _Micro()),
            meso=meso.get(name, _Meso()),
            support=support.get(name, _Support()),
            start=start,
            end=end,
            rows=rows,
        )
        for name in names
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "basis": "active_commit_facts + active_work_packages + graph-derived work_day_correlations",
        },
        "methodology": {
            "velocity_model": "micro code activity, meso landed work packages, macro artifact shape, and cross-source support kept separate",
            "commit_caveat": "commit counts are heartbeat signals only and vary by commit granularity",
            "scope_caveat": "scope proxies are comparative within project history, not absolute value or usefulness",
            "cross_source_caveat": "ActivityWatch, terminal, Polylogue, raw-log, and GitHub are support dimensions, not velocity scores",
            "no_scalar_velocity": "no single velocity_score is emitted",
        },
        "inputs": {
            "active_commit_facts": str(commit_facts_file or "active_commit_facts.json"),
            "active_work_packages": str(work_packages_file or "active_work_packages.json"),
            "work_day_correlations": "computed for the same date window",
        },
        "projects": project_rows,
        "summary": _summary(project_rows),
    }


def run_project_velocity_windows(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Materialize project velocity windows."""
    payload = build_project_velocity_windows(
        start=start,
        end=end,
        projects=projects,
        commit_facts_file=commit_facts_file,
        work_packages_file=work_packages_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _load_commit_payload(
    *,
    path: str | PathLike[str] | None,
) -> Mapping[str, Any]:
    return load_json_object(
        path or resolve_analysis_path("active_commit_facts.json"),
        label="active commit facts",
    )


def _load_work_payload(
    *,
    path: str | PathLike[str] | None,
) -> Mapping[str, Any]:
    return load_json_object(
        path or resolve_analysis_path("active_work_packages.json"),
        label="active work packages",
    )


def _correlation_rows(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None,
) -> tuple[CorrelatedWorkDay, ...]:
    # Use the base graph (no analysis overlay) so velocity_windows never sees
    # the velocity claims it is about to write.
    graph = build_base_evidence_graph(
        start=start,
        end=end,
        projects=projects,
    )
    return work_day_correlations(start=start, end=end, graph=graph)


def _project_meta(*payloads: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        projects = payload.get("projects")
        if not isinstance(projects, list):
            continue
        for row in projects:
            if not isinstance(row, dict):
                continue
            project = row.get("project")
            if isinstance(project, str):
                result.setdefault(project, {}).update(row)
    return result


def _micro_by_project(payload: Mapping[str, Any]) -> dict[str, _Micro]:
    grouped: dict[str, _Micro] = defaultdict(_Micro)
    commits = payload.get("commits")
    if not isinstance(commits, list):
        return {}
    for row in commits:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project:
            continue
        bucket = grouped[project]
        bucket.commits += 1
        day = str(row.get("date") or "")
        if day:
            bucket.active_days.add(day)
        bucket.files_changed += int(row.get("files_changed") or 0)
        bucket.classified_files_changed += int(row.get("classified_files_changed") or 0)
        bucket.conventional_kinds[str(row.get("conventional_kind") or "other")] += 1
        bucket.categories.update(_counter(row.get("categories")))
        bucket.path_roots.update(_counter(row.get("path_roots")))
        subject = str(row.get("subject") or "")
        if subject:
            bucket.subjects.append(subject)
    return dict(grouped)


def _meso_by_project(payload: Mapping[str, Any]) -> dict[str, _Meso]:
    grouped: dict[str, _Meso] = {}
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return {}
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project:
            continue
        bucket = _Meso()
        raw_packages = row.get("packages")
        packages = raw_packages if isinstance(raw_packages, list) else []
        for package in packages:
            if not isinstance(package, dict):
                continue
            bucket.packages += 1
            commit_count = int(package.get("commit_count") or 0)
            bucket.commits += commit_count
            unit_type = str(package.get("unit_type") or "unknown")
            bucket.unit_types[unit_type] += 1
            if unit_type == "github_thread":
                bucket.github_thread_packages += 1
            elif unit_type == "single_commit":
                bucket.single_commit_packages += 1
            else:
                bucket.heuristic_packages += 1
            bucket.total_scope_geom += float(package.get("scope_geom") or 0.0)
            bucket.total_durability_adjusted_scope += float(package.get("durability_adjusted_scope") or 0.0)
            bucket.surfaces.update(str(surface) for surface in package.get("top_surfaces") or ())
            bucket.top_packages.append(package)
        bucket.top_packages.sort(key=lambda item: -float(item.get("durability_adjusted_scope") or 0.0))
        grouped[project] = bucket
    return grouped


def _support_by_project(rows: Sequence[CorrelatedWorkDay]) -> dict[str, _Support]:
    grouped: dict[str, _Support] = defaultdict(_Support)
    for row in rows:
        bucket = grouped[row.project]
        bucket.active_days.add(row.date.isoformat())
        if row.has_cross_source_support:
            bucket.cross_source_days.add(row.date.isoformat())
        bucket.sources.update(row.sources)
        for idx, left in enumerate(row.sources):
            for right in row.sources[idx + 1:]:
                bucket.source_pairs[f"{left}+{right}"] += 1
        bucket.github_lifecycles.update(row.github_lifecycles)
        bucket.ai_sessions += row.ai_session_count
        bucket.focus_minutes += row.focus_minutes
        bucket.shell_commands += row.shell_command_count
        bucket.raw_log_count += row.raw_log_count
        bucket.github_refs += len(row.github_refs)
    return dict(grouped)


def _project_row(
    project: str,
    *,
    meta: Mapping[str, Any],
    micro: _Micro,
    meso: _Meso,
    support: _Support,
    start: date,
    end: date,
    rows: Sequence[CorrelatedWorkDay] = (),
) -> dict[str, Any]:
    project_rows = [r for r in rows if r.project == project]
    cross_ratio = round(len(support.cross_source_days) / len(support.active_days), 3) if support.active_days else 0.0
    support_level = _support_level(micro=micro, meso=meso, support=support)
    return {
        "project": project,
        "status": "available" if bool(meta.get("exists", True)) else "missing",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "path": meta.get("path"),
        "default_branch": meta.get("default_branch"),
        "micro_effort": {
            "active_days": len(micro.active_days),
            "commit_count": micro.commits,
            "files_changed": micro.files_changed,
            "classified_files_changed": micro.classified_files_changed,
            "conventional_kinds": dict(micro.conventional_kinds.most_common()),
            "category_touches": dict(micro.categories.most_common()),
            "top_path_roots": dict(micro.path_roots.most_common(10)),
            "top_subjects": micro.subjects[-8:],
        },
        "meso_delivery": {
            "landed_package_count": meso.packages,
            "github_thread_package_count": meso.github_thread_packages,
            "heuristic_package_count": meso.heuristic_packages,
            "single_commit_package_count": meso.single_commit_packages,
            "total_scope_geom": round(meso.total_scope_geom, 6),
            "total_durability_adjusted_scope": round(meso.total_durability_adjusted_scope, 6),
            "unit_types": dict(meso.unit_types.most_common()),
            "top_surfaces": dict(meso.surfaces.most_common(10)),
            "top_packages": [_package_summary(package, rows=project_rows) for package in meso.top_packages[:8]],
        },
        "cross_source_support": {
            "correlated_days": len(support.active_days),
            "cross_source_days": len(support.cross_source_days),
            "cross_source_ratio": cross_ratio,
            "sources": [source for source, _ in support.sources.most_common()],
            "source_counts": dict(support.sources.most_common()),
            "source_pair_counts": dict(support.source_pairs.most_common(12)),
            "github_lifecycles": dict(support.github_lifecycles.most_common()),
            "ai_session_count": support.ai_sessions,
            "focus_hours": round(support.focus_minutes / 60.0, 2),
            "shell_command_count": support.shell_commands,
            "raw_log_count": support.raw_log_count,
            "github_ref_count": support.github_refs,
        },
        "interpretation_signals": {
            "support_level": support_level,
            "primary_motion": _primary_motion(micro, meso),
            "weaknesses": _weaknesses(micro, meso, support),
            "not_claimed": ["business value", "adoption", "best use of time", "single scalar velocity"],
        },
        "caveats": _caveats(micro, meso, support),
    }


def _package_summary(
    package: Mapping[str, Any],
    *,
    rows: Sequence[CorrelatedWorkDay] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "work_package_id": package.get("work_package_id"),
        "unit_type": package.get("unit_type"),
        "label": package.get("label"),
        "commit_count": package.get("commit_count"),
        "durability_adjusted_scope": package.get("durability_adjusted_scope"),
        "refs": package.get("refs"),
        "top_surfaces": package.get("top_surfaces"),
    }
    result["cross_source_support"] = _package_support(package, rows)
    return result


def _package_support(
    package: Mapping[str, Any],
    rows: Sequence[CorrelatedWorkDay],
) -> dict[str, Any]:
    """Per-package cross-source support evidence from CorrelatedWorkDay rows."""
    commit_shas = set(package.get("commit_shas") or ())
    refs = package.get("refs") or {}
    first_date_str = str(package.get("first_date") or "")
    last_date_str = str(package.get("last_date") or "")

    first_date: date | None = date.fromisoformat(first_date_str) if first_date_str else None
    last_date: date | None = date.fromisoformat(last_date_str) if last_date_str else None

    expected_refs: set[str] = set()
    if isinstance(refs, dict):
        for pr_num in refs.get("prs") or ():
            expected_refs.add(f"pr#{pr_num}")
        for issue_num in refs.get("issues") or ():
            expected_refs.add(f"issue#{issue_num}")

    package_top_surfaces = tuple(str(s) for s in (package.get("top_surfaces") or ()) if s)

    has_handles = bool(commit_shas or expected_refs or first_date)
    empty_result: dict[str, Any] = {
        "support_days": 0,
        "strong_match_days": 0,
        "sources": [],
        "source_counts": {},
        "source_pair_counts": {},
        "github_lifecycles": {},
        "ai_session_count": 0,
        "focus_hours": 0.0,
        "shell_command_count": 0,
        "raw_log_count": 0,
        "github_ref_count": 0,
        "match_reasons": {"commit_overlap": 0, "github_ref_overlap": 0, "date_overlap": 0, "kind_match": 0},
        "kind_breakdown": {},
        "kind_breakdown_weighted": {},
        "support_level": "weak",
        "caveats": [],
    }

    if not has_handles:
        empty_result["caveats"] = ["package has no cross-reference handles"]
        return empty_result

    matched_rows: list[tuple[CorrelatedWorkDay, set[str]]] = []
    for row in rows:
        reasons: set[str] = set()
        if commit_shas and any(sha in commit_shas for sha in row.commit_shas):
            reasons.add("commit_overlap")
        if expected_refs and any(ref in expected_refs for ref in row.github_refs):
            reasons.add("github_ref_overlap")
        if first_date is not None and last_date is not None:
            padded_start = first_date - timedelta(days=1)
            padded_end = last_date + timedelta(days=1)
            if padded_start <= row.date <= padded_end:
                reasons.add("date_overlap")
        if reasons:
            matched_rows.append((row, reasons))

    if not matched_rows:
        empty_result["caveats"] = ["no correlated work-day rows matched this package"]
        return empty_result

    support_days: set[str] = set()
    strong_match_days: set[str] = set()
    source_counter: Counter[str] = Counter()
    source_pair_counter: Counter[str] = Counter()
    lifecycle_counter: Counter[str] = Counter()
    kind_breakdown_counter: Counter[str] = Counter()
    kind_weighted_acc: dict[str, float] = {}
    ai_session_count = 0
    focus_minutes = 0.0
    shell_command_count = 0
    raw_log_count = 0
    github_ref_count = 0
    reason_counter: Counter[str] = Counter()

    for row, reasons in matched_rows:
        day_str = row.date.isoformat()
        support_days.add(day_str)
        if "commit_overlap" in reasons or "github_ref_overlap" in reasons:
            strong_match_days.add(day_str)
        # Arc B.3: kind_match reason fires when the row's dominant kind
        # plausibly aligns with the package's surface mix (tests/ ↔ testing,
        # docs/ ↔ research/conversation, manifests ↔ implementation).
        if _kind_aligns_with_surfaces(row.ai_kind_breakdown, package_top_surfaces):
            reasons.add("kind_match")
        for reason in reasons:
            reason_counter[reason] += 1
        source_counter.update(row.sources)
        for idx, left in enumerate(row.sources):
            for right in row.sources[idx + 1:]:
                pair = f"{left}+{right}" if left <= right else f"{right}+{left}"
                source_pair_counter[pair] += 1
        lifecycle_counter.update(row.github_lifecycles)
        for kind, count in row.ai_kind_breakdown:
            kind_breakdown_counter[kind] += count
        for kind, weight in row.ai_kind_weighted:
            kind_weighted_acc[kind] = kind_weighted_acc.get(kind, 0.0) + weight
        ai_session_count += row.ai_session_count
        focus_minutes += row.focus_minutes
        shell_command_count += row.shell_command_count
        raw_log_count += row.raw_log_count
        github_ref_count += len(row.github_refs)

    has_strong = bool(strong_match_days)
    non_git_sources = [s for s in source_counter if s != "git"]
    if has_strong and non_git_sources:
        support_level = "strong"
    elif len(source_counter) >= 2:
        support_level = "moderate"
    else:
        support_level = "weak"

    caveats: list[str] = []
    if not has_strong:
        caveats.append("no commit or GitHub ref overlap — matched by date proximity only")

    return {
        "support_days": len(support_days),
        "strong_match_days": len(strong_match_days),
        "sources": [s for s, _ in source_counter.most_common()],
        "source_counts": dict(source_counter),
        "source_pair_counts": dict(source_pair_counter),
        "github_lifecycles": dict(lifecycle_counter),
        "ai_session_count": ai_session_count,
        "focus_hours": round(focus_minutes / 60.0, 2),
        "shell_command_count": shell_command_count,
        "raw_log_count": raw_log_count,
        "github_ref_count": github_ref_count,
        "match_reasons": {
            "commit_overlap": reason_counter["commit_overlap"],
            "github_ref_overlap": reason_counter["github_ref_overlap"],
            "date_overlap": reason_counter["date_overlap"],
            "kind_match": reason_counter["kind_match"],
        },
        "kind_breakdown": dict(kind_breakdown_counter),
        "kind_breakdown_weighted": {k: round(v, 2) for k, v in kind_weighted_acc.items()},
        "support_level": support_level,
        "caveats": caveats,
    }


_KIND_TO_SURFACE_HINTS: dict[str, tuple[str, ...]] = {
    "testing": ("tests", "test", "spec", "fixtures"),
    "research": ("docs", "doc"),
    "conversation": ("docs", "doc"),
    "implementation": ("src", "lib", "lynchpin", "polylogue"),
    "review": ("review", "rfc"),
    "debugging": ("src", "lib"),
    "dependency_management": ("Cargo.toml", "pyproject.toml", "package.json", "flake.nix"),
}


def _kind_aligns_with_surfaces(
    kind_breakdown: tuple[tuple[str, int], ...],
    top_surfaces: tuple[str, ...],
) -> bool:
    """True iff the dominant kind plausibly aligns with the package's surfaces.

    Conservative: requires both that a kind has at least one observation in
    this row AND that one of its surface hints appears in any package
    surface path. Returns False on empty inputs so absence is never spun
    into a kind_match.
    """
    if not kind_breakdown or not top_surfaces:
        return False
    surfaces_lower = tuple(s.lower() for s in top_surfaces)
    for kind, count in kind_breakdown:
        if count <= 0:
            continue
        for hint in _KIND_TO_SURFACE_HINTS.get(kind, ()):
            hint_lower = hint.lower()
            if any(hint_lower in surface for surface in surfaces_lower):
                return True
    return False


def _support_level(*, micro: _Micro, meso: _Meso, support: _Support) -> str:
    if meso.packages >= 3 and len(support.cross_source_days) >= 3 and support.ai_sessions:
        return "strong"
    if micro.commits or meso.packages or len(support.active_days) >= 2:
        return "moderate"
    return "weak"


def _primary_motion(micro: _Micro, meso: _Meso) -> list[str]:
    motion: list[str] = []
    for kind, _ in micro.conventional_kinds.most_common(4):
        if kind != "other":
            motion.append(kind)
    for unit_type, _ in meso.unit_types.most_common(3):
        if unit_type not in motion:
            motion.append(unit_type)
    return motion[:5]


def _weaknesses(micro: _Micro, meso: _Meso, support: _Support) -> list[str]:
    weaknesses: list[str] = []
    if micro.commits and not support.ai_sessions:
        weaknesses.append("no correlated AI-session support")
    if micro.commits and not support.focus_minutes:
        weaknesses.append("no correlated ActivityWatch focus")
    if meso.heuristic_packages > meso.github_thread_packages:
        weaknesses.append("many work packages are heuristic clusters")
    if meso.packages and meso.single_commit_packages > meso.packages / 2:
        weaknesses.append("many packages are single commits")
    if not support.cross_source_days and (micro.commits or meso.packages):
        weaknesses.append("landed code has no same-day cross-source support")
    return weaknesses[:6]


def _caveats(micro: _Micro, meso: _Meso, support: _Support) -> list[dict[str, str]]:
    caveats = [
        {
            "source": "git",
            "severity": "partial",
            "message": "Commit and file counts are granularity-dependent and not standalone velocity.",
        },
        {
            "source": "analysis",
            "severity": "partial",
            "message": "Scope uses active work-package proxies, not line churn or external outcome value.",
        },
    ]
    if meso.heuristic_packages:
        caveats.append({
            "source": "analysis",
            "severity": "partial",
            "message": "Some packages are heuristic clusters because commits lacked explicit GitHub refs.",
        })
    if support.sources and "activitywatch" not in support.sources:
        caveats.append({
            "source": "activitywatch",
            "severity": "partial",
            "message": "No correlated focus evidence for this project/window.",
        })
    if not support.sources and (micro.commits or meso.packages):
        caveats.append({
            "source": "correlation",
            "severity": "partial",
            "message": "Code movement exists without graph-derived cross-source corroboration.",
        })
    return caveats


def _summary(projects: Sequence[dict[str, Any]]) -> dict[str, Any]:
    strong = []
    moderate = []
    weak = []
    for row in projects:
        level = _nested_str(row, "interpretation_signals", "support_level")
        project = str(row.get("project") or "")
        if level == "strong":
            strong.append(project)
        elif level == "moderate":
            moderate.append(project)
        else:
            weak.append(project)
    ordered = sorted(
        projects,
        key=lambda row: -float(_nested_number(row, "meso_delivery", "total_durability_adjusted_scope")),
    )
    return {
        "project_count": len(projects),
        "available_project_count": sum(1 for row in projects if row.get("status") == "available"),
        "strong_support_projects": strong,
        "moderate_support_projects": moderate,
        "weak_support_projects": weak,
        "top_scope_projects": [
            {
                "project": row.get("project"),
                "support_level": _nested_str(row, "interpretation_signals", "support_level"),
                "commit_count": _nested_number(row, "micro_effort", "commit_count"),
                "landed_package_count": _nested_number(row, "meso_delivery", "landed_package_count"),
                "total_durability_adjusted_scope": _nested_number(row, "meso_delivery", "total_durability_adjusted_scope"),
                "cross_source_days": _nested_number(row, "cross_source_support", "cross_source_days"),
            }
            for row in ordered[:12]
        ],
    }


def _counter(value: object) -> Counter[str]:
    if not isinstance(value, dict):
        return Counter()
    result: Counter[str] = Counter()
    for key, count in value.items():
        try:
            result[str(key)] = int(str(count))
        except ValueError:
            continue
    return result


def _nested_str(row: Mapping[str, Any], section: str, key: str) -> str:
    value = row.get(section)
    if not isinstance(value, dict):
        return ""
    return str(value.get(key) or "")


def _nested_number(row: Mapping[str, Any], section: str, key: str) -> float:
    value = row.get(section)
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["build_project_velocity_windows", "run_project_velocity_windows"]
