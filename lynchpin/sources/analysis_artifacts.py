"""Generated analysis artifact source.

The analysis package writes durable JSON/Markdown products under the configured
analysis output directory. This source makes those products readable as ordinary
Lynchpin evidence without importing the analysis producers or rerunning them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..core.config import get_config
from ..core.cache import write_text_if_changed
from ..core.projects import canonical_project_name
from .analysis_artifact_claims import claims_for_artifact
from .analysis_artifact_helpers import dict_or_empty, list_or_empty
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim

__all__ = [
    "AnalysisArtifact",
    "AnalysisClaim",
    "analysis_claims",
    "artifact_inventory",
    "latest_artifacts",
]


_PROJECT_BY_PREFIX = {
    "sinex": "sinex",
    "polylogue": "polylogue",
}

_PROJECT_BY_NAME = {
    "analysis_snapshot": "sinity-lynchpin",
    "analysis_status": "sinity-lynchpin",
    "lynchpin_self_metrics": "sinity-lynchpin",
    "machine_below_analysis": "sinity-lynchpin",
    "machine_telemetry_analysis": "sinity-lynchpin",
    "machine_assumption_checks": "sinity-lynchpin",
    "machine_mechanism_hypotheses": "sinity-lynchpin",
    "machine_instrumentation_gaps": "sinity-lynchpin",
    "machine_calibration_fixtures": "sinity-lynchpin",
    "machine_measurement_system": "sinity-lynchpin",
    "machine_analysis_materialization_report": "sinity-lynchpin",
    "machine_negative_controls": "sinity-lynchpin",
    "claim_calibration": "sinity-lynchpin",
    "code_history_claims": "sinity-lynchpin",
    "google_takeout_retrospective": "sinity-lynchpin",
    "personal_interest_trace": "sinity-lynchpin",
    "workflow_mechanics": "sinity-lynchpin",
}

_PROJECTS_BY_NAME = {
    "change_surface_map": ("sinex",),
    "commit_facts": ("sinex",),
    "commit_shards": ("sinex",),
    "cross_project_metrics": ("sinex", "polylogue", "sinity-lynchpin"),
    "active_rust_workspace_graph": ("sinex",),
    "dependency_map": ("sinex",),
    "ecosystem_comparison": ("sinex", "polylogue"),
    "ecosystem_dashboard": ("sinex", "polylogue"),
    "hotspot_map": ("sinex",),
    "module_map": ("sinex",),
    "project-maps": ("sinex",),
    "work_package_scope": ("sinex", "polylogue"),
    "machine_attribution_claims": ("sinity-lynchpin", "sinex"),
}

_INVENTORY_MANIFEST = ".analysis_artifact_inventory.json"
_INVENTORY_SCHEMA_VERSION = 1
_CLAIM_MANIFEST = ".analysis_claim_inventory.json"
_CLAIM_SCHEMA_VERSION = 1


def artifact_inventory(root: Path | None = None) -> tuple[AnalysisArtifact, ...]:
    """Return generated analysis products visible under the analysis root."""
    base = root or get_config().analysis_output_dir
    if not base.exists():
        return ()
    signature = _artifact_signature(base)
    manifest = _load_inventory_manifest(base, signature)
    if manifest is not None:
        return manifest
    artifacts = [_artifact(path, base=base) for path in _artifact_paths(base)]
    _write_inventory_manifest(base, signature, artifacts)
    return tuple(
        sorted(artifacts, key=lambda artifact: (artifact.project or "", artifact.name))
    )


def latest_artifacts(
    *,
    projects: Iterable[str] | None = None,
    root: Path | None = None,
) -> tuple[AnalysisArtifact, ...]:
    """Return readable artifacts, optionally restricted to project-affine rows."""
    selected = set(projects or ())
    artifacts = tuple(
        item for item in artifact_inventory(root) if item.status == "available"
    )
    if selected:
        artifacts = tuple(item for item in artifacts if set(item.projects) & selected)
    return artifacts


def analysis_claims(
    *,
    projects: Iterable[str] | None = None,
    root: Path | None = None,
    exclude_names: Iterable[str] = (),
    artifacts: Iterable[AnalysisArtifact] | None = None,
) -> tuple[AnalysisClaim, ...]:
    """Return selected typed claims extracted from generated analysis products."""
    excluded = set(exclude_names)
    selected = set(projects or ())
    claims: list[AnalysisClaim] = []
    source_artifacts = (
        tuple(artifacts)
        if artifacts is not None
        else latest_artifacts(projects=selected or None, root=root)
    )
    signature = _claim_signature(source_artifacts)
    cache_root = _claims_cache_root(source_artifacts, root=root)
    if cache_root is not None:
        cached = _load_claim_manifest(cache_root, signature)
        if cached is not None:
            return _filter_claims(cached, selected=selected, excluded=excluded)

    for artifact in source_artifacts:
        if artifact.name in excluded or artifact.kind != "json":
            continue
        if artifact.status != "available":
            continue
        payload = _json_payload(artifact.path)
        if payload is None:
            continue
        claims.extend(claims_for_artifact(artifact, payload, selected=set()))
    result = tuple(claims)
    if cache_root is not None:
        _write_claim_manifest(cache_root, signature, result)
    return _filter_claims(result, selected=selected, excluded=excluded)


def _filter_claims(
    claims: Iterable[AnalysisClaim],
    *,
    selected: set[str],
    excluded: set[str],
) -> tuple[AnalysisClaim, ...]:
    return tuple(
        claim
        for claim in claims
        if claim.artifact_name not in excluded
        and (not selected or claim.project in selected)
    )


def _claim_signature(artifacts: Iterable[AnalysisArtifact]) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for artifact in artifacts:
        rows.append(
            {
                "name": artifact.name,
                "kind": artifact.kind,
                "status": artifact.status,
                "size_bytes": artifact.size_bytes,
                "mtime_ns": artifact.path.stat().st_mtime_ns
                if artifact.path.exists()
                else 0,
            }
        )
    return sorted(rows, key=lambda row: str(row["name"]))


def _claims_cache_root(
    artifacts: Iterable[AnalysisArtifact], *, root: Path | None
) -> Path | None:
    if root is not None:
        return root
    artifact_paths = [artifact.path.parent for artifact in artifacts]
    if not artifact_paths:
        return get_config().analysis_output_dir
    try:
        return Path(os.path.commonpath([str(path) for path in artifact_paths]))
    except ValueError:
        return None


def _load_claim_manifest(
    root: Path,
    signature: list[dict[str, int | str]],
) -> tuple[AnalysisClaim, ...] | None:
    path = root / _CLAIM_MANIFEST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != _CLAIM_SCHEMA_VERSION:
        return None
    if payload.get("signature") != signature:
        return None
    rows = payload.get("claims")
    if not isinstance(rows, list):
        return None
    claims = []
    for item in rows:
        claim = _claim_from_manifest(item)
        if claim is None:
            return None
        claims.append(claim)
    return tuple(claims)


def _write_claim_manifest(
    root: Path,
    signature: list[dict[str, int | str]],
    claims: Iterable[AnalysisClaim],
) -> None:
    payload = {
        "schema_version": _CLAIM_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "signature": signature,
        "claims": [_claim_to_manifest(claim) for claim in claims],
    }
    try:
        write_text_if_changed(
            root / _CLAIM_MANIFEST,
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
        )
    except OSError:
        return


def _claim_to_manifest(claim: AnalysisClaim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "artifact_name": claim.artifact_name,
        "claim_type": claim.claim_type,
        "project": claim.project,
        "summary": claim.summary,
        "payload": claim.payload,
        "confidence": claim.confidence,
        "generated_at": claim.generated_at.isoformat()
        if claim.generated_at is not None
        else None,
    }


def _claim_from_manifest(item: object) -> AnalysisClaim | None:
    if not isinstance(item, dict):
        return None
    try:
        generated_raw = item.get("generated_at")
        generated_at = (
            datetime.fromisoformat(str(generated_raw)) if generated_raw else None
        )
        payload = item.get("payload")
        return AnalysisClaim(
            id=str(item["id"]),
            artifact_name=str(item["artifact_name"]),
            claim_type=str(item["claim_type"]),
            project=str(item["project"]),
            summary=str(item["summary"]),
            payload=dict(payload) if isinstance(payload, dict) else {},
            confidence=float(item["confidence"]),
            generated_at=generated_at,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _artifact_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != _INVENTORY_MANIFEST
        and path.name != _CLAIM_MANIFEST
        and path.suffix.lower() in {".json", ".md", ".html"}
    )


def _artifact_signature(root: Path) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for path in _artifact_paths(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "name": path.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return sorted(rows, key=lambda row: str(row["name"]))


def _load_inventory_manifest(
    root: Path,
    signature: list[dict[str, int | str]],
) -> tuple[AnalysisArtifact, ...] | None:
    path = root / _INVENTORY_MANIFEST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != _INVENTORY_SCHEMA_VERSION:
        return None
    if payload.get("signature") != signature:
        return None
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    rows = []
    for item in artifacts:
        artifact = _artifact_from_manifest(item, root=root)
        if artifact is None:
            return None
        rows.append(artifact)
    return tuple(sorted(rows, key=lambda artifact: (artifact.project or "", artifact.name)))


def _write_inventory_manifest(
    root: Path,
    signature: list[dict[str, int | str]],
    artifacts: Iterable[AnalysisArtifact],
) -> None:
    payload = {
        "schema_version": _INVENTORY_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "signature": signature,
        "artifacts": [_artifact_to_manifest(artifact, root=root) for artifact in artifacts],
    }
    try:
        write_text_if_changed(
            root / _INVENTORY_MANIFEST,
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
        )
    except OSError:
        return


def _artifact_to_manifest(artifact: AnalysisArtifact, *, root: Path) -> dict[str, Any]:
    return {
        "name": artifact.name,
        "path": artifact.path.relative_to(root).as_posix()
        if artifact.path.is_relative_to(root)
        else str(artifact.path),
        "kind": artifact.kind,
        "projects": list(artifact.projects),
        "size_bytes": artifact.size_bytes,
        "modified_at": artifact.modified_at.isoformat(),
        "generated_at": artifact.generated_at.isoformat()
        if artifact.generated_at is not None
        else None,
        "top_level_keys": list(artifact.top_level_keys),
        "brief": artifact.brief,
        "references": list(artifact.references),
        "status": artifact.status,
        "reason": artifact.reason,
    }


def _artifact_from_manifest(item: object, *, root: Path) -> AnalysisArtifact | None:
    if not isinstance(item, dict):
        return None
    try:
        modified_at = datetime.fromisoformat(str(item["modified_at"]))
        generated_raw = item.get("generated_at")
        generated_at = (
            datetime.fromisoformat(str(generated_raw)) if generated_raw else None
        )
        raw_path = Path(str(item.get("path") or item["name"]))
        path = raw_path if raw_path.is_absolute() else root / raw_path
        return AnalysisArtifact(
            name=str(item["name"]),
            path=path,
            kind=str(item["kind"]),
            projects=tuple(str(value) for value in item.get("projects", ())),
            size_bytes=int(item["size_bytes"]),
            modified_at=modified_at,
            generated_at=generated_at,
            top_level_keys=tuple(str(value) for value in item.get("top_level_keys", ())),
            brief=str(item["brief"]) if item.get("brief") is not None else None,
            references=tuple(str(value) for value in item.get("references", ())),
            status=str(item.get("status") or "available"),
            reason=str(item["reason"]) if item.get("reason") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _artifact(path: Path, *, base: Path) -> AnalysisArtifact:
    stat = path.stat()
    name = path.relative_to(base).as_posix()
    modified_at = datetime.fromtimestamp(stat.st_mtime).astimezone()
    metadata = _metadata(path, base=base)
    projects = metadata.get("projects")
    return AnalysisArtifact(
        name=name,
        path=path,
        kind=path.suffix.lower().lstrip("."),
        projects=projects
        if isinstance(projects, tuple)
        else _projects_for_artifact(path),
        size_bytes=stat.st_size,
        modified_at=modified_at,
        generated_at=metadata.get("generated_at"),
        top_level_keys=metadata.get("keys", ()),
        brief=metadata.get("brief"),
        references=metadata.get("references", ()),
        status=metadata.get("status", "available"),
        reason=metadata.get("reason"),
    )


def _metadata(path: Path, *, base: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".md":
        return _markdown_metadata(path)
    if path.suffix.lower() != ".json":
        return {"keys": (), "references": ()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "partial",
            "reason": f"{type(exc).__name__}: {exc}",
            "keys": (),
            "references": (),
        }
    if not isinstance(payload, dict):
        return {"keys": (), "references": ()}
    generated = payload.get("generated_at_utc") or payload.get("generated_at")
    generated_at = _parse_datetime(generated) if isinstance(generated, str) else None
    return {
        "generated_at": generated_at,
        "keys": tuple(sorted(str(key) for key in payload.keys())),
        "brief": _brief(path.stem, payload),
        "projects": _payload_projects(path.stem, payload),
        "references": _artifact_references(
            payload, base=base, current_name=path.relative_to(base).as_posix()
        ),
    }


def _json_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _markdown_metadata(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {
            "status": "partial",
            "reason": f"{type(exc).__name__}: {exc}",
            "keys": (),
            "references": (),
        }
    for line in lines:
        if line.startswith("# "):
            title = line.lstrip("#").strip()
            if title:
                return {"keys": (), "references": (), "brief": f"notes: {title}"}
    return {"keys": (), "references": ()}


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _brief(stem: str, payload: dict[str, Any]) -> str | None:
    if stem == "analysis_status":
        families = payload.get("families")
        if isinstance(families, dict):
            counts: dict[str, int] = {}
            for row in families.values():
                if isinstance(row, dict):
                    status = str(row.get("status") or "unknown")
                    counts[status] = counts.get(status, 0) + 1
            if counts:
                return "families " + ", ".join(
                    f"{key}={value}" for key, value in sorted(counts.items())
                )
    if stem == "ecosystem_dashboard":
        narratives = payload.get("narratives")
        if isinstance(narratives, list):
            titles = [
                str(row.get("title"))
                for row in narratives
                if isinstance(row, dict) and row.get("title")
            ]
            if titles:
                return "narratives: " + "; ".join(titles[:3])
    if stem == "ecosystem_comparison":
        headline = payload.get("headline")
        if isinstance(headline, dict):
            ratios = headline.get("ratios")
            if isinstance(ratios, list) and ratios:
                names = [
                    str(row.get("metric"))
                    for row in ratios
                    if isinstance(row, dict) and row.get("metric")
                ]
                if names:
                    return "headline ratios: " + ", ".join(names[:3])
    if stem == "work_package_scope":
        ecosystems = payload.get("ecosystems")
        if isinstance(ecosystems, dict):
            rows = []
            for name, section in ecosystems.items():
                if isinstance(section, dict):
                    summary = section.get("summary")
                    if isinstance(summary, dict):
                        rows.append(f"{name}={summary.get('unit_count', 0)} units")
            if rows:
                return ", ".join(rows)
    if stem == "current_state_context_pack":
        mode = payload.get("mode")
        projects = payload.get("projects")
        claims = payload.get("claims")
        project_count = len(projects) if isinstance(projects, list) else 0
        claim_count = len(claims) if isinstance(claims, list) else 0
        if isinstance(mode, str):
            return f"{mode} context pack, {project_count} project slices, {claim_count} supported claims"
    if stem == "current_state_narrative":
        sections = payload.get("sections")
        section_count = len(sections) if isinstance(sections, list) else 0
        moment_count = int(payload.get("moment_count") or 0)
        project_count = int(payload.get("project_count") or 0)
        return f"narrative report: {section_count} sections, {moment_count} moments, {project_count} projects"
    if stem == "cross_project_metrics":
        projects = payload.get("projects")
        if isinstance(projects, dict):
            return f"{len(projects)} project metric rows"
    if stem == "active_project_snapshot":
        projects = payload.get("projects")
        window = payload.get("window")
        project_count = len(projects) if isinstance(projects, list) else 0
        if isinstance(window, dict):
            return f"{project_count} active project snapshots, {window.get('start')} to {window.get('end')}"
        return f"{project_count} active project snapshots"
    if stem == "active_code_inventory":
        projects = payload.get("projects")
        project_count = len(projects) if isinstance(projects, list) else 0
        total_code = 0
        for row in projects or []:
            if isinstance(row, dict):
                total_code += int(row.get("total_code_lines") or 0)
        return (
            f"{project_count} project code inventories, {total_code:,} total code lines"
        )
    if stem == "active_python_complexity":
        projects = payload.get("projects")
        project_count = len(projects) if isinstance(projects, list) else 0
        total_funcs = 0
        for row in projects or []:
            if isinstance(row, dict):
                total_funcs += int(
                    (row.get("summary") or {}).get("total_functions") or 0
                )
        return f"{project_count} Python projects complexity, {total_funcs:,} functions analyzed"
    if stem == "active_python_import_graph":
        projects = payload.get("projects")
        project_count = len(projects) if isinstance(projects, list) else 0
        total_modules = 0
        for row in projects or []:
            if isinstance(row, dict):
                total_modules += int(row.get("module_count") or 0)
        return f"{project_count} Python import graphs, {total_modules:,} modules"
    if stem == "active_rust_workspace_graph":
        projects = payload.get("projects")
        project_count = len(projects) if isinstance(projects, list) else 0
        total_crates = 0
        for row in projects or []:
            if isinstance(row, dict):
                total_crates += int(row.get("workspace_crate_count") or 0)
        return f"{project_count} Rust workspace graphs, {total_crates} total crates"
    if stem == "active_python_dependency_hygiene":
        projects = payload.get("projects")
        project_count = len(projects) if isinstance(projects, list) else 0
        advisory_count = 0
        observed_count = 0
        for row in projects or []:
            if isinstance(row, dict):
                advisories = list_or_empty(
                    dict_or_empty(row.get("audit")).get("advisories")
                )
                advisory_count += len(advisories)
                observed_count += sum(
                    1
                    for adv in advisories
                    if isinstance(adv, dict) and adv.get("observed_import")
                )
        return (
            f"{project_count} Python dependency hygiene rows, "
            f"{advisory_count} advisories ({observed_count} observed in imports)"
        )
    if stem == "active_rust_dependency_hygiene":
        workspaces = payload.get("workspaces")
        workspace_count = len(workspaces) if isinstance(workspaces, list) else 0
        advisory_count = 0
        for row in workspaces or []:
            if isinstance(row, dict):
                advisory_count += len(
                    list_or_empty(dict_or_empty(row.get("audit")).get("advisories"))
                )
        return f"{workspace_count} Rust dependency hygiene rows, {advisory_count} advisories"
    if stem == "active_commit_facts":
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return (
                f"{summary.get('commit_count', 0)} active commits across "
                f"{summary.get('available_project_count', 0)} projects"
            )
    if stem == "active_file_change_facts":
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return (
                f"{summary.get('file_change_count', 0)} active file changes "
                f"({summary.get('classified_file_change_count', 0)} classified)"
            )
    if stem == "active_work_packages":
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return (
                f"{summary.get('package_count', 0)} active work packages across "
                f"{summary.get('available_project_count', 0)} projects"
            )
    if stem == "project_velocity_windows":
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return (
                f"{summary.get('project_count', 0)} velocity windows; "
                f"strong={len(list_or_empty(summary.get('strong_support_projects')))}, "
                f"moderate={len(list_or_empty(summary.get('moderate_support_projects')))}"
            )
    if stem == "code_history_claims":
        claims = payload.get("claims")
        claim_count = len(claims) if isinstance(claims, list) else int(payload.get("claim_count") or 0)
        window = dict_or_empty(payload.get("window"))
        return f"{claim_count} code-history claims, {window.get('start')} to {window.get('end')}"
    if stem == "claim_calibration":
        return (
            f"{int(payload.get('claim_count') or 0)} claims calibrated, "
            f"{int(payload.get('issue_count') or 0)} issues"
        )
    if stem == "google_takeout_retrospective":
        return (
            f"{int(payload.get('event_count') or 0)} Google Takeout events, "
            f"{int(payload.get('active_days') or 0)} active days"
        )
    if stem == "personal_interest_trace":
        return f"{int(payload.get('topic_count') or 0)} weak personal-interest topics"
    if stem == "workflow_mechanics":
        return (
            f"{int(payload.get('invocation_count') or 0)} work invocations, "
            f"{int(payload.get('retry_chain_count') or 0)} retry chains"
        )
    if stem == "machine_telemetry_analysis":
        coverage = payload.get("coverage")
        daily = payload.get("daily")
        if isinstance(coverage, dict):
            return (
                f"{coverage.get('sample_count', 0)} machine metric samples, "
                f"{len(daily) if isinstance(daily, list) else 0} daily profiles"
            )
    if stem == "machine_below_analysis":
        system = payload.get("system")
        processes = payload.get("top_processes")
        cgroups = payload.get("top_cgroups")
        return (
            f"{len(system) if isinstance(system, list) else 0} below windows, "
            f"{len(processes) if isinstance(processes, list) else 0} process rows, "
            f"{len(cgroups) if isinstance(cgroups, list) else 0} cgroup rows"
        )
    return None


def _payload_projects(stem: str, payload: dict[str, Any]) -> tuple[str, ...] | None:
    if stem not in {
        "current_state_context_pack",
        "active_project_snapshot",
        "active_code_inventory",
        "active_python_complexity",
        "active_python_import_graph",
        "active_python_dependency_hygiene",
        "active_rust_workspace_graph",
        "active_rust_dependency_hygiene",
        "active_commit_facts",
        "active_file_change_facts",
        "active_work_packages",
        "code_history_claims",
        "project_velocity_windows",
    }:
        return None
    rows = (
        payload.get("workspaces")
        if stem == "active_rust_dependency_hygiene"
        else payload.get("claims")
        if stem == "code_history_claims"
        else payload.get("projects")
    )
    if not isinstance(rows, list):
        return None
    names = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        project = canonical_project_name(item.get("project"))
        if project is not None:
            names.append(project)
    return tuple(sorted(set(names)))


def _artifact_references(
    payload: dict[str, Any], *, base: Path, current_name: str
) -> tuple[str, ...]:
    references: set[str] = set()
    base_resolved = base.resolve()
    for value in _walk_json_scalars(payload):
        if not isinstance(value, str):
            continue
        candidate = _artifact_reference(value, base=base_resolved)
        if candidate is not None and candidate != current_name:
            references.add(candidate)
    return tuple(sorted(references))


def _artifact_reference(value: str, *, base: Path) -> str | None:
    suffix = Path(value).suffix.lower()
    if suffix not in {".json", ".md", ".html"}:
        return None
    path = Path(value)
    if not path.is_absolute():
        candidate = base / path
        if not candidate.is_file():
            return None
        return path.as_posix()
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return None


def _walk_json_scalars(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_json_scalars(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_json_scalars(item)
        return
    yield value


def _projects_for_artifact(path: Path) -> tuple[str, ...]:
    stem = path.stem
    if stem in _PROJECTS_BY_NAME:
        return _PROJECTS_BY_NAME[stem]
    if stem in _PROJECT_BY_NAME:
        return (_PROJECT_BY_NAME[stem],)
    for prefix, project in _PROJECT_BY_PREFIX.items():
        if stem == prefix or stem.startswith(f"{prefix}_"):
            return (project,)
    return ()
