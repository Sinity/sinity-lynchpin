"""Structural rule findings via ast-grep cross-referenced with recent file changes.

Produces `active_structural_findings.json` — ast-grep matches on active projects
promoted only when the matched path changed recently.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parent.parent / "tool_rules" / "ast-grep"

_PYTHON_RULES = (
    _RULES_DIR / "python-subprocess-no-timeout.yml",
    _RULES_DIR / "python-bare-except.yml",
)
_RUST_RULES = (
    _RULES_DIR / "rust-unwrap-in-runtime.yml",
)
_PYTHON_EXTS = {".py"}
_RUST_EXTS = {".rs"}


def build_active_structural_findings(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot = load_json_object(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )
    changes_payload = load_json_object(
        file_changes_file or resolve_analysis_path("active_file_change_facts.json"),
        label="active file-change facts",
    )

    selected = set(projects or ())
    snapshot_projects = _project_map(snapshot, selected)
    recently_changed = _changed_paths(changes_payload, selected)

    findings: list[dict[str, Any]] = []
    for project_name, project_path in sorted(snapshot_projects.items()):
        root = Path(project_path)
        if not root.exists():
            continue
        ext = _primary_extension(snapshot, project_name)
        if ext in _PYTHON_EXTS:
            findings.extend(_run_rules(
                project_name, str(root), _PYTHON_RULES, recently_changed))
        elif ext in _RUST_EXTS:
            findings.extend(_run_rules(
                project_name, str(root), _RUST_RULES, recently_changed))

    project_set = sorted({f["project"] for f in findings})
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "ast-grep scan with curated structural rules",
            "promotion": "findings promoted only when the matched path appears in active_file_change_facts",
            "caveat": "structural matches require human interpretation; false positives possible",
        },
        "inputs": {
            "rules_dir": str(_RULES_DIR),
            "python_rules": [r.name for r in _PYTHON_RULES],
            "rust_rules": [r.name for r in _RUST_RULES],
            "active_file_change_facts": str(file_changes_file or "active_file_change_facts.json"),
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
        },
        "projects": project_set,
        "findings": findings,
    }


def run_active_structural_findings(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_structural_findings(
        start=start, end=end, projects=projects,
        snapshot_file=snapshot_file, file_changes_file=file_changes_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _run_rules(
    project: str,
    project_path: str,
    rule_paths: tuple[Path, ...],
    recently_changed: dict[str, set[str]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    skipped_lines = 0
    for rule_path in rule_paths:
        if not rule_path.exists():
            continue
        try:
            result = subprocess.run(
                ["ast-grep", "scan", "--rule", str(rule_path), "--json=stream",
                 project_path],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                match = json.loads(line)
            except json.JSONDecodeError:
                skipped_lines += 1
                continue
            if not isinstance(match, dict):
                continue
            file_path = str(match.get("file") or "")
            rule_id = str(match.get("ruleId") or "")
            severity = str(match.get("severity") or "warning")
            message = str(match.get("message") or "")
            line_num = (match.get("range") or {}).get("start", {}).get("line")
            try:
                line_num = int(line_num)
            except (TypeError, ValueError):
                line_num = 0

            rel_path = _relative_path(file_path, project_path)
            is_recent = _is_recently_changed(project, rel_path, recently_changed)

            findings.append({
                "project": project,
                "rule_id": rule_id,
                "severity": severity,
                "path": rel_path,
                "line": line_num,
                "message": message,
                "recently_changed": is_recent,
                "caveats": ["structural match requires human interpretation"],
            })
    if skipped_lines:
        logger.warning(
            "structural_findings: %d ast-grep JSON lines skipped for %s",
            skipped_lines, project,
        )
    return findings


def _relative_path(file_path: str, project_root: str) -> str:
    root = str(project_root).rstrip("/") + "/"
    if file_path.startswith(root):
        return file_path[len(root):]
    return file_path


def _is_recently_changed(
    project: str, path: str, recently_changed: dict[str, set[str]],
) -> bool:
    paths = recently_changed.get(project)
    if paths is None:
        return False
    return path in paths or path.lstrip("/") in paths


def _changed_paths(
    payload: dict[str, Any] | None, selected: set[str],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in _list(payload, "file_changes"):
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if selected and project not in selected:
            continue
        path = str(row.get("path") or "")
        if path:
            result.setdefault(project, set()).add(path)
    return result


def _project_map(snapshot: dict[str, Any] | None, selected: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _list(snapshot, "projects"):
        if not isinstance(row, dict):
            continue
        name = str(row.get("project") or "")
        if selected and name not in selected:
            continue
        path = str(row.get("path") or "")
        if row.get("exists") is False:
            continue
        if path:
            result[name] = path
    return result


def _primary_extension(snapshot: dict[str, Any] | None, project: str) -> str:
    for row in _list(snapshot, "projects"):
        if not isinstance(row, dict):
            continue
        if row.get("project") == project:
            exts = row.get("structure", {}).get("extensions", {})
            if isinstance(exts, dict) and exts:
                return sorted(exts, key=lambda e: -exts[e].get("files", 0))[0]
    return ""


def _list(payload: dict[str, Any] | None, key: str) -> list[Any]:
    if payload is None:
        return []
    result = payload.get(key)
    return result if isinstance(result, list) else []


__all__ = ["build_active_structural_findings", "run_active_structural_findings"]
