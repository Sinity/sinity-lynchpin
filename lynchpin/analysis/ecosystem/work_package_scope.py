"""Native scope-weighted work-package model across Sinex and Polylogue."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.canonical import load_analysis_spec
from ..core.commit_stats import collect_commit_stats, parse_iso_datetime
from lynchpin.core.io import load_json, resolve_analysis_path, resolve_artifact_path, save_json

_CONVENTIONAL_RE = re.compile(r"^([a-z]+)(?:\(([^)]+)\))?!?:\s*(.*)$", re.IGNORECASE)
_GENERIC_SUBJECT_RE = re.compile(r"^(merge|wip|update|test|commit|fix|refactor|docs|chore)\b", re.IGNORECASE)


def resolve_author(name: str) -> str:
    """Identity passthrough — kept as a named hook for ecosystem-specific author normalization."""
    return name


def _surface_for_path(ecosystem: str, path: str) -> str:
    parts = [part for part in (path or "").replace("\\", "/").split("/") if part]
    if not parts:
        return "unknown"

    if ecosystem == "sinex":
        if parts[0] == "crate" and len(parts) >= 3:
            return parts[2]
        if parts[0] == "src" and len(parts) >= 2:
            return parts[1]
        if parts[0] == "tests":
            return f"tests/{parts[1]}" if len(parts) >= 2 else "tests"
        return parts[0]

    if ecosystem == "polylogue":
        if parts[0] == "polylogue":
            return parts[1] if len(parts) >= 2 else "polylogue"
        if parts[0] == "tests":
            return f"tests/{parts[1]}" if len(parts) >= 2 else "tests"
        if parts[0] in {"unit", "integration"}:
            return f"tests/{parts[0]}"
        return parts[0]

    return parts[0]


def _scope_geom(artifact_churn_kloc: float, artifact_paths: int, breadth: int) -> float:
    value = round(
        ((1.0 + artifact_churn_kloc) * (1.0 + artifact_paths) * (1.0 + breadth)) ** (1.0 / 3.0) - 1.0,
        6,
    )
    return float(value)


def _durability_adjusted_scope(scope_geom: float, survival_surface_share: float) -> float:
    return round(scope_geom * (0.5 + 0.5 * survival_surface_share), 6)


def _conventional_signature(subject: str) -> str:
    match = _CONVENTIONAL_RE.match(subject or "")
    if not match:
        return "other"
    kind = match.group(1).lower()
    scope = (match.group(2) or "").strip().lower()
    return f"{kind}({scope})" if scope else kind


def _subject_tail(subject: str) -> str:
    match = _CONVENTIONAL_RE.match(subject or "")
    if not match:
        return (subject or "").strip()
    tail = (match.group(3) or "").strip()
    return tail or (subject or "").strip()


def _best_subject(commits: list[dict[str, Any]]) -> str:
    for commit in commits:
        subject = (commit.get("subject") or "").strip()
        if not subject:
            continue
        tail = _subject_tail(subject)
        if not _GENERIC_SUBJECT_RE.match(subject) and len(tail) >= 12:
            return subject
    return (commits[0].get("subject") or "").strip() if commits else ""


def _span_days(first: datetime | None, last: datetime | None) -> int:
    if first is None or last is None:
        return 0
    return max(0, (last.date() - first.date()).days)


def _survival_surface_share(repo: str, paths: set[str]) -> float:
    if not paths:
        return 0.0
    surviving = sum(1 for path in paths if (Path(repo) / path).exists())
    return round(surviving / len(paths), 6)


def _commit_record(ecosystem: str, commit: dict[str, Any]) -> dict[str, Any]:
    paths = sorted(commit.get("files") or [])
    surface_counter = Counter(_surface_for_path(ecosystem, path) for path in paths)
    dominant_surface = (
        sorted(surface_counter.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if surface_counter
        else "unknown"
    )
    dt = parse_iso_datetime(str(commit.get("date") or ""))
    return {
        "sha": commit["sha"],
        "author": resolve_author(commit.get("author") or ""),
        "date": commit.get("date"),
        "dt": dt,
        "subject": commit.get("subject", ""),
        "additions": commit.get("additions", 0),
        "deletions": commit.get("deletions", 0),
        "lines_changed": commit.get("lines_changed", 0),
        "files_changed": commit.get("files_changed", len(paths)),
        "paths": paths,
        "surface_counter": surface_counter,
        "surfaces": sorted(surface_counter),
        "dominant_surface": dominant_surface,
        "subject_signature": _conventional_signature(commit.get("subject", "")),
    }


def _build_package(
    *,
    ecosystem: str,
    package_id: str,
    unit_type: str,
    commits: list[dict[str, Any]],
    repo_snapshot: str,
    label: str | None = None,
    archive_support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path_set: set[str] = set()
    surface_counter: Counter[str] = Counter()
    authors: Counter[str] = Counter()
    churn = 0
    additions = 0
    deletions = 0
    first_dt: datetime | None = None
    last_dt: datetime | None = None

    for commit in commits:
        path_set.update(commit["paths"])
        surface_counter.update(commit["surface_counter"])
        authors[commit["author"]] += 1
        churn += commit["lines_changed"]
        additions += commit["additions"]
        deletions += commit["deletions"]
        dt = commit.get("dt")
        if dt is None:
            continue
        if first_dt is None or dt < first_dt:
            first_dt = dt
        if last_dt is None or dt > last_dt:
            last_dt = dt

    breadth = len(surface_counter)
    artifact_paths = len(path_set)
    artifact_churn_kloc = round(churn / 1000.0, 3)
    scope_geom = _scope_geom(artifact_churn_kloc, artifact_paths, breadth)
    survival_surface_share = _survival_surface_share(repo_snapshot, path_set)

    row = {
        "work_package_id": package_id,
        "ecosystem": ecosystem,
        "unit_type": unit_type,
        "label": label or _best_subject(commits) or package_id,
        "commit_count": len(commits),
        "author_count": len(authors),
        "authors": [name for name, _ in authors.most_common(5)],
        "first_date": first_dt.date().isoformat() if first_dt else None,
        "last_date": last_dt.date().isoformat() if last_dt else None,
        "span_days": _span_days(first_dt, last_dt),
        "artifact_churn_kloc": artifact_churn_kloc,
        "artifact_paths": artifact_paths,
        "breadth": breadth,
        "scope_geom": scope_geom,
        "survival_surface_share": survival_surface_share,
        "durability_adjusted_scope": _durability_adjusted_scope(scope_geom, survival_surface_share),
        "top_surfaces": [name for name, _ in surface_counter.most_common(5)],
        "dominant_surface": surface_counter.most_common(1)[0][0] if surface_counter else "unknown",
        "total_additions": additions,
        "total_deletions": deletions,
        "total_churn": churn,
        "commit_shas": [commit["sha"] for commit in commits[:40]],
        "best_subject": _best_subject(commits),
    }
    if archive_support is not None:
        row["archive_support"] = archive_support
    return row


def _cluster_commit_records(records: list[dict[str, Any]], *, gap_days: int = 7) -> list[list[dict[str, Any]]]:
    if not records:
        return []

    ordered = sorted(records, key=lambda row: row["date"] or "")
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [ordered[0]]
    current_surfaces = set(ordered[0]["surfaces"])
    current_dominant = ordered[0]["dominant_surface"]
    current_signature = ordered[0]["subject_signature"]

    for record in ordered[1:]:
        prev = current[-1]
        prev_dt = prev.get("dt")
        record_dt = record.get("dt")
        gap = 0
        if prev_dt is not None and record_dt is not None:
            gap = max(0, (record_dt.date() - prev_dt.date()).days)
        overlap = bool(current_surfaces & set(record["surfaces"]))
        same_dominant = record["dominant_surface"] == current_dominant
        same_signature = record["subject_signature"] == current_signature and record["subject_signature"] != "other"
        if gap <= gap_days and (overlap or same_dominant or (same_signature and gap <= 2)):
            current.append(record)
            current_surfaces.update(record["surfaces"])
            if record["dominant_surface"] != "unknown":
                current_dominant = record["dominant_surface"]
            if record["subject_signature"] != "other":
                current_signature = record["subject_signature"]
            continue
        clusters.append(current)
        current = [record]
        current_surfaces = set(record["surfaces"])
        current_dominant = record["dominant_surface"]
        current_signature = record["subject_signature"]

    clusters.append(current)
    return clusters


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(float(ordered[len(ordered) // 2]), 6)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered)) - 1)))
    return round(float(ordered[index]), 6)


def _ecosystem_summary(
    ecosystem: str,
    packages: list[dict[str, Any]],
    *,
    unit_definition: str,
    archive_support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scopes = [row["scope_geom"] for row in packages]
    durability = [row["durability_adjusted_scope"] for row in packages]
    summary = {
        "ecosystem": ecosystem,
        "unit_definition": unit_definition,
        "unit_count": len(packages),
        "total_scope_geom": round(sum(scopes), 6),
        "total_durability_adjusted_scope": round(sum(durability), 6),
        "median_scope_geom": _median(scopes),
        "p90_scope_geom": _percentile(scopes, 0.9),
        "median_artifact_paths": _median([row["artifact_paths"] for row in packages]),
        "median_breadth": _median([row["breadth"] for row in packages]),
        "median_commit_count": _median([row["commit_count"] for row in packages]),
        "top_work_packages": packages[:20],
    }
    if archive_support is not None:
        summary["archive_support"] = archive_support
    return summary


def _build_git_cluster_packages(
    *,
    ecosystem: str,
    repo: str,
    branch: str,
    archive_support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    commits = collect_commit_stats(repo_dir=repo, branch=branch, keep_files=True)
    records = [_commit_record(ecosystem, commit) for commit in commits]
    clusters = _cluster_commit_records(records)
    packages = []
    for index, cluster in enumerate(clusters, start=1):
        label = _best_subject(cluster) or f"{ecosystem} cluster {index}"
        packages.append(
            _build_package(
                ecosystem=ecosystem,
                package_id=f"{ecosystem}-cluster:{index:04d}",
                unit_type="contiguous_change_cluster",
                commits=cluster,
                repo_snapshot=repo,
                label=label,
                archive_support=archive_support if ecosystem == "polylogue" and index <= 20 else None,
            )
        )
    packages.sort(key=lambda row: row["scope_geom"], reverse=True)
    return {
        "summary": _ecosystem_summary(
            ecosystem,
            packages,
            unit_definition="contiguous change clusters split by time gaps and subsystem continuity",
            archive_support=archive_support,
        ),
        "packages": packages,
    }


def _polylogue_archive_support(spec: dict[str, Any]) -> dict[str, Any]:
    data = load_json(resolve_artifact_path(spec, "polylogue_metrics"))
    recent = data.get("archive", {}).get("recent_90d", {})
    return {
        "recent_session_count": recent.get("session_count", 0),
        "recent_total_messages": recent.get("total_messages", 0),
        "recent_total_words": recent.get("total_words", 0),
        "providers": recent.get("providers", {}),
        "projects": recent.get("projects", {}),
    }


def build_work_package_scope(spec_path: str) -> dict[str, Any]:
    spec = load_analysis_spec(spec_path)

    sinex = _build_git_cluster_packages(
        ecosystem="sinex",
        repo=spec["sinex"]["repo"],
        branch=spec["sinex"]["branch"],
    )
    poly_archive_support = _polylogue_archive_support(spec)
    polylogue = _build_git_cluster_packages(
        ecosystem="polylogue",
        repo=spec["polylogue"]["repo"],
        branch=spec["polylogue"]["branch"],
        archive_support=poly_archive_support,
    )

    ecosystems = {
        "sinex": sinex,
        "polylogue": polylogue,
    }
    ranking = []
    for ecosystem, payload in ecosystems.items():
        for row in payload["packages"][:80]:
            ranking.append(
                {
                    "ecosystem": ecosystem,
                    "work_package_id": row["work_package_id"],
                    "label": row["label"],
                    "unit_type": row["unit_type"],
                    "scope_geom": row["scope_geom"],
                    "durability_adjusted_scope": row["durability_adjusted_scope"],
                    "commit_count": row["commit_count"],
                    "dominant_surface": row["dominant_surface"],
                }
            )
    ranking.sort(key=lambda row: row["scope_geom"], reverse=True)

    summary_rows = [
        {
            "ecosystem": ecosystem,
            "unit_count": payload["summary"]["unit_count"],
            "total_scope_geom": payload["summary"]["total_scope_geom"],
            "median_scope_geom": payload["summary"]["median_scope_geom"],
            "p90_scope_geom": payload["summary"]["p90_scope_geom"],
            "median_artifact_paths": payload["summary"]["median_artifact_paths"],
            "median_breadth": payload["summary"]["median_breadth"],
        }
        for ecosystem, payload in ecosystems.items()
    ]
    summary_rows.sort(key=lambda row: row["total_scope_geom"], reverse=True)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "unit_definitions": {
                "sinex": "contiguous git change clusters over the live repo",
                "polylogue": "contiguous git change clusters over the live repo, augmented with archive activity context",
            },
            "scope_proxy": {
                "artifact_churn": "total changed lines scaled to KLOC",
                "artifact_paths": "distinct touched paths",
                "breadth": "distinct touched subsystem surfaces",
                "formula": "scope_geom = geometric_mean(1 + artifact_churn_kloc, 1 + artifact_paths, 1 + breadth) - 1",
            },
            "durability_proxy": {
                "field": "survival_surface_share",
                "definition": "share of touched paths still present in the current checked-out repo snapshot",
                "formula": "durability_adjusted_scope = scope_geom * (0.5 + 0.5 * survival_surface_share)",
            },
        },
        "ecosystems": ecosystems,
        "comparison": {
            "summary": summary_rows,
            "top_work_packages": ranking[:40],
        },
    }


def run_work_package_scope(spec_path: str, out_file: str | Path) -> dict[str, Any]:
    payload = build_work_package_scope(spec_path)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload
