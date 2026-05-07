"""Project-agnostic active work-package materializer."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ..active.git_facts import build_active_commit_facts
from ..core.io import load_json_if_exists, resolve_analysis_path, save_json

_DEFAULT_GAP_DAYS = 2


@dataclass(frozen=True)
class _CommitRow:
    project: str
    sha: str
    short_sha: str
    timestamp: str
    subject: str
    author: str
    conventional_kind: str
    conventional_scope: str | None
    conventional_signature: str
    paths: tuple[str, ...]
    categories: Counter[str]
    path_roots: Counter[str]
    refs: dict[str, tuple[int, ...]]

    @property
    def dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None

    @property
    def day(self) -> str:
        return self.timestamp[:10] or "unknown"

    @property
    def surfaces(self) -> tuple[str, ...]:
        if self.categories:
            return tuple(sorted(self.categories))
        if self.path_roots:
            return tuple(sorted(self.path_roots))
        return ("unknown",)

    @property
    def dominant_surface(self) -> str:
        surface_counts = self.categories or self.path_roots
        if not surface_counts:
            return "unknown"
        return surface_counts.most_common(1)[0][0]


def build_active_work_packages(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_payload: Mapping[str, Any] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build active work packages from active commit facts."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    payload = dict(commit_payload or _load_or_build_commit_payload(
        start=start,
        end=end,
        projects=projects,
        commit_facts_file=commit_facts_file,
    ))
    project_meta = _project_meta(payload)
    rows = _commit_rows(payload)
    grouped: dict[str, list[_CommitRow]] = defaultdict(list)
    for row in rows:
        if projects and row.project not in set(projects):
            continue
        grouped[row.project].append(row)

    project_rows: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []
    for project, commits in sorted(grouped.items()):
        meta = project_meta.get(project, {})
        project_packages = _packages_for_project(project, commits, project_path=Path(str(meta.get("path") or "")))
        packages.extend(project_packages)
        project_rows.append({
            "project": project,
            "path": meta.get("path"),
            "default_branch": meta.get("default_branch"),
            "status": "available",
            "commit_count": len(commits),
            "package_count": len(project_packages),
            "packages": project_packages,
        })

    for project, meta in sorted(project_meta.items()):
        if grouped.get(project) or (projects and project not in set(projects)):
            continue
        project_rows.append({
            "project": project,
            "path": meta.get("path"),
            "default_branch": meta.get("default_branch"),
            "status": meta.get("status", "missing"),
            "commit_count": 0,
            "package_count": 0,
            "packages": [],
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "git_history": "active_commit_facts/default-branch first-parent",
        },
        "methodology": {
            "project_scope": "active project registry unless projects are explicitly selected",
            "unit_selection_order": [
                "github_thread",
                "conventional_scope_burst",
                "surface_temporal_cluster",
                "single_commit",
            ],
            "scope_proxy": "geometric mean over commit count, unique paths, and surface breadth; no line churn is inferred",
            "linkage_policy": "packages are commit-rooted; non-git evidence should link later by refs, time, project, and surface",
            "caveat": "work packages are landed-code units, not value judgments or final task lifecycle classifications",
        },
        "inputs": {"active_commit_facts": str(commit_facts_file or "active_commit_facts.json")},
        "projects": sorted(project_rows, key=lambda row: str(row["project"])),
        "summary": _summary(packages, project_rows),
    }


def run_active_work_packages(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Materialize active work packages from active commit facts."""
    payload = build_active_work_packages(
        start=start,
        end=end,
        projects=projects,
        commit_facts_file=commit_facts_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _load_or_build_commit_payload(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None,
    commit_facts_file: str | PathLike[str] | None,
) -> Mapping[str, Any]:
    if commit_facts_file is not None:
        payload = load_json_if_exists(commit_facts_file)
        if isinstance(payload, dict):
            return payload
    default_path = Path(resolve_analysis_path("active_commit_facts.json"))
    payload = load_json_if_exists(default_path)
    if isinstance(payload, dict):
        window = payload.get("window")
        if isinstance(window, dict) and window.get("start") == start.isoformat() and window.get("end") == end.isoformat():
            return payload
    return build_active_commit_facts(start=start, end=end, projects=projects)


def _project_meta(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = row.get("project")
        if isinstance(project, str):
            result[project] = row
    return result


def _commit_rows(payload: Mapping[str, Any]) -> tuple[_CommitRow, ...]:
    commits = payload.get("commits")
    if not isinstance(commits, list):
        return ()
    rows: list[_CommitRow] = []
    for row in commits:
        if not isinstance(row, dict):
            continue
        project = row.get("project")
        sha = row.get("sha")
        timestamp = row.get("timestamp")
        if not isinstance(project, str) or not isinstance(sha, str) or not isinstance(timestamp, str):
            continue
        rows.append(
            _CommitRow(
                project=project,
                sha=sha,
                short_sha=str(row.get("short_sha") or sha[:7]),
                timestamp=timestamp,
                subject=str(row.get("subject") or ""),
                author=str(row.get("author") or ""),
                conventional_kind=str(row.get("conventional_kind") or "other"),
                conventional_scope=_optional_str(row.get("conventional_scope")),
                conventional_signature=str(row.get("conventional_signature") or "other"),
                paths=_string_tuple(row.get("paths")),
                categories=_counter(row.get("categories")),
                path_roots=_counter(row.get("path_roots")),
                refs=_refs(row.get("github_refs")),
            )
        )
    return tuple(sorted(rows, key=lambda item: (item.project, item.timestamp, item.sha)))


def _packages_for_project(project: str, commits: Sequence[_CommitRow], *, project_path: Path) -> list[dict[str, Any]]:
    remaining = {commit.sha: commit for commit in commits}
    packages: list[dict[str, Any]] = []

    for (kind, number), group in _github_groups(remaining.values()).items():
        packages.append(_package(project, "github_thread", f"{kind}#{number}", group, project_path=project_path))
        for commit in group:
            remaining.pop(commit.sha, None)

    conventional_groups = _conventional_groups(remaining.values())
    for signature, clusters in conventional_groups.items():
        for index, group in enumerate(clusters, start=1):
            if len(group) < 2:
                continue
            packages.append(_package(project, "conventional_scope_burst", f"{signature}:{index}", group, project_path=project_path))
            for commit in group:
                remaining.pop(commit.sha, None)

    singles: list[_CommitRow] = []
    for index, group in enumerate(_surface_clusters(remaining.values()), start=1):
        for commit in group:
            remaining.pop(commit.sha, None)
        if len(group) < 2:
            singles.extend(group)
            continue
        dominant = _dominant_surface(group)
        packages.append(_package(project, "surface_temporal_cluster", f"{dominant}:{index}", group, project_path=project_path))

    for commit in sorted((*singles, *remaining.values()), key=lambda item: (item.timestamp, item.sha)):
        packages.append(_package(project, "single_commit", commit.short_sha, [commit], project_path=project_path))

    return sorted(packages, key=lambda row: (row["first_at"] or "", row["work_package_id"]))


def _github_groups(commits: Iterable[_CommitRow]) -> dict[tuple[str, int], list[_CommitRow]]:
    groups: dict[tuple[str, int], list[_CommitRow]] = defaultdict(list)
    for commit in commits:
        if commit.refs["prs"]:
            groups[("pr", commit.refs["prs"][0])].append(commit)
        elif commit.refs["issues"]:
            groups[("issue", commit.refs["issues"][0])].append(commit)
    return dict(groups)


def _conventional_groups(commits: Iterable[_CommitRow]) -> dict[str, list[list[_CommitRow]]]:
    by_signature: dict[str, list[_CommitRow]] = defaultdict(list)
    for commit in commits:
        if commit.conventional_scope and commit.conventional_signature != "other":
            by_signature[commit.conventional_signature].append(commit)
    return {
        signature: _split_by_gap(rows, gap_days=_DEFAULT_GAP_DAYS)
        for signature, rows in by_signature.items()
    }


def _surface_clusters(commits: Iterable[_CommitRow]) -> list[list[_CommitRow]]:
    ordered = sorted(commits, key=lambda item: (item.timestamp, item.sha))
    clusters: list[list[_CommitRow]] = []
    current: list[_CommitRow] = []
    current_surfaces: set[str] = set()
    for commit in ordered:
        if not current:
            current = [commit]
            current_surfaces = set(commit.surfaces)
            continue
        previous = current[-1]
        gap_ok = _gap_days(previous, commit) <= _DEFAULT_GAP_DAYS
        surface_ok = bool(current_surfaces & set(commit.surfaces)) or _dominant_surface(current) == commit.dominant_surface
        if gap_ok and surface_ok:
            current.append(commit)
            current_surfaces.update(commit.surfaces)
            continue
        clusters.append(current)
        current = [commit]
        current_surfaces = set(commit.surfaces)
    if current:
        clusters.append(current)
    return clusters


def _split_by_gap(commits: Sequence[_CommitRow], *, gap_days: int) -> list[list[_CommitRow]]:
    ordered = sorted(commits, key=lambda item: (item.timestamp, item.sha))
    clusters: list[list[_CommitRow]] = []
    current: list[_CommitRow] = []
    for commit in ordered:
        if current and _gap_days(current[-1], commit) > gap_days:
            clusters.append(current)
            current = []
        current.append(commit)
    if current:
        clusters.append(current)
    return clusters


def _package(
    project: str,
    unit_type: str,
    unit_key: str,
    commits: Sequence[_CommitRow],
    *,
    project_path: Path,
) -> dict[str, Any]:
    ordered = sorted(commits, key=lambda item: (item.timestamp, item.sha))
    path_set = {path for commit in ordered for path in commit.paths}
    surface_counter = Counter(surface for commit in ordered for surface in commit.surfaces)
    path_root_counter = Counter(root for commit in ordered for root in commit.path_roots)
    authors = Counter(commit.author for commit in ordered if commit.author)
    kinds = Counter(commit.conventional_kind for commit in ordered)
    refs = _merge_refs(ordered)
    first = ordered[0] if ordered else None
    last = ordered[-1] if ordered else None
    scope_geom = _scope_geom(len(ordered), len(path_set), len(surface_counter))
    survival_share = _survival_share(project_path, path_set)
    package_id = _package_id(project, unit_type, unit_key, first)
    confidence = _confidence(unit_type)
    return {
        "work_package_id": package_id,
        "project": project,
        "unit_type": unit_type,
        "unit_key": unit_key,
        "label": _best_subject(ordered) or unit_key,
        "status": "github_referenced" if unit_type == "github_thread" else "local_only",
        "lifecycle": "landed_default_branch",
        "confidence": confidence,
        "first_at": first.timestamp if first else None,
        "last_at": last.timestamp if last else None,
        "first_date": first.day if first else None,
        "last_date": last.day if last else None,
        "span_days": _span_days(first, last),
        "commit_count": len(ordered),
        "commit_shas": [commit.sha for commit in ordered],
        "authors": [author for author, _ in authors.most_common(5)],
        "dominant_surface": surface_counter.most_common(1)[0][0] if surface_counter else "unknown",
        "top_surfaces": [surface for surface, _ in surface_counter.most_common(8)],
        "path_roots": [root for root, _ in path_root_counter.most_common(12)],
        "artifact_paths": len(path_set),
        "path_touch_count": sum(len(commit.paths) for commit in ordered),
        "breadth": len(surface_counter),
        "scope_geom": scope_geom,
        "survival_surface_share": survival_share,
        "durability_adjusted_scope": _durability_adjusted_scope(scope_geom, survival_share),
        "direction_mix": _direction_mix(kinds),
        "refs": refs,
        "links": {
            "github_item_ids": [],
            "conversation_ids": [],
            "focus_node_ids": [],
            "terminal_node_ids": [],
            "evidence_node_ids": [],
        },
        "caveats": _caveats(unit_type, survival_share),
    }


def _scope_geom(commit_count: int, artifact_paths: int, breadth: int) -> float:
    return round(float(((1 + commit_count) * (1 + artifact_paths) * (1 + breadth)) ** (1 / 3) - 1), 6)


def _durability_adjusted_scope(scope_geom: float, survival_share: float) -> float:
    return round(scope_geom * (0.5 + 0.5 * survival_share), 6)


def _survival_share(project_path: Path, paths: set[str]) -> float:
    if not paths or not str(project_path):
        return 0.0
    surviving = sum(1 for path in paths if (project_path / path).exists())
    return round(surviving / len(paths), 6)


def _span_days(first: _CommitRow | None, last: _CommitRow | None) -> int:
    if first is None or last is None:
        return 0
    try:
        first_day = date.fromisoformat(first.day)
        last_day = date.fromisoformat(last.day)
    except ValueError:
        return 0
    return max(0, (last_day - first_day).days)


def _gap_days(left: _CommitRow, right: _CommitRow) -> int:
    left_dt = left.dt
    right_dt = right.dt
    if left_dt is None or right_dt is None:
        return 999
    return abs((right_dt.date() - left_dt.date()).days)


def _dominant_surface(commits: Sequence[_CommitRow]) -> str:
    counter = Counter(surface for commit in commits for surface in commit.surfaces)
    return counter.most_common(1)[0][0] if counter else "unknown"


def _best_subject(commits: Sequence[_CommitRow]) -> str:
    for commit in commits:
        tail = _subject_tail(commit.subject)
        if len(tail) >= 12 and not re.match(r"^(merge|wip|update|test|commit)$", tail, re.IGNORECASE):
            return commit.subject
    return commits[0].subject if commits else ""


def _subject_tail(subject: str) -> str:
    match = re.match(r"^[a-z]+(?:\([^)]+\))?!?:\s*(.*)$", subject or "", re.IGNORECASE)
    return (match.group(1) if match else subject or "").strip()


def _merge_refs(commits: Sequence[_CommitRow]) -> dict[str, list[int]]:
    prs = sorted({number for commit in commits for number in commit.refs["prs"]})
    issues = sorted({number for commit in commits for number in commit.refs["issues"]})
    return {"prs": prs, "issues": issues, "urls": []}


def _direction_mix(kinds: Counter[str]) -> dict[str, float]:
    total = sum(kinds.values())
    if not total:
        return {}
    return {kind: round(count / total, 6) for kind, count in sorted(kinds.items())}


def _caveats(unit_type: str, survival_share: float) -> list[dict[str, str]]:
    caveats = [
        {
            "source": "git",
            "severity": "partial",
            "message": "Package scope uses commit/path/surface facts; line churn is not inferred.",
        }
    ]
    if unit_type != "github_thread":
        caveats.append({
            "source": "git",
            "severity": "partial",
            "message": "Package grouping is heuristic because commits lacked explicit GitHub thread refs.",
        })
    if survival_share < 0.5:
        caveats.append({
            "source": "filesystem",
            "severity": "partial",
            "message": "Less than half of touched paths survive in the current checkout.",
        })
    return caveats


def _confidence(unit_type: str) -> float:
    return {
        "github_thread": 0.9,
        "conventional_scope_burst": 0.72,
        "surface_temporal_cluster": 0.62,
        "single_commit": 0.55,
    }[unit_type]


def _package_id(project: str, unit_type: str, unit_key: str, first: _CommitRow | None) -> str:
    if unit_type == "single_commit" and first is not None:
        return f"wp:{project}:commit:{first.short_sha}"
    return f"wp:{project}:{_slug(unit_type)}:{_slug(unit_key)}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-") or "unknown"


def _summary(packages: Sequence[dict[str, Any]], projects: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(packages, key=lambda row: (-float(row.get("durability_adjusted_scope") or 0), str(row.get("work_package_id"))))
    return {
        "project_count": len(projects),
        "available_project_count": sum(1 for project in projects if project.get("status") == "available"),
        "package_count": len(packages),
        "top_work_packages": [
            {
                "work_package_id": row["work_package_id"],
                "project": row["project"],
                "label": row["label"],
                "unit_type": row["unit_type"],
                "commit_count": row["commit_count"],
                "durability_adjusted_scope": row["durability_adjusted_scope"],
                "refs": row["refs"],
            }
            for row in ordered[:24]
        ],
    }


def _refs(value: object) -> dict[str, tuple[int, ...]]:
    if not isinstance(value, dict):
        return {"prs": (), "issues": ()}
    return {
        "prs": _int_tuple(value.get("prs")),
        "issues": _int_tuple(value.get("issues")),
    }


def _counter(value: object) -> Counter[str]:
    if not isinstance(value, dict):
        return Counter()
    return Counter({str(key): int(count) for key, count in value.items() if _is_int_like(count)})


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _int_tuple(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    result: list[int] = []
    for item in value:
        if isinstance(item, int):
            result.append(item)
    return tuple(sorted(result))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_int_like(value: object) -> bool:
    try:
        int(str(value))
    except (TypeError, ValueError):
        return False
    return True


__all__ = ["build_active_work_packages", "run_active_work_packages"]
