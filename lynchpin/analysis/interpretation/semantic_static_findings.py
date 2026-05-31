"""Curated Semgrep risk findings cross-referenced with recent file changes.

Produces ``active_semantic_static_findings.json``. Mirrors the pattern of
``active_structural_findings`` (ast-grep) but with semgrep's pattern engine,
restricted to a small curated rule pack maintained in
``lynchpin/analysis/tool_rules/semgrep/lynchpin-privacy/``.

Findings are promoted as ``recently_changed=True`` only when the path
appears in ``active_file_change_facts``; everything else is reported but
flagged as background noise.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json

_RULES_DIR = Path(__file__).resolve().parent.parent / "tool_rules" / "semgrep" / "lynchpin-privacy"


def build_active_semantic_static_findings(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build curated static risk findings.

    The historical function name is kept because the artifact name is already
    consumed by the analysis pipeline. The payload is static risk data, not
    semantic interpretation.
    """
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

    semgrep_path = _which_semgrep()
    rules_available = sorted(p.name for p in _RULES_DIR.glob("*.yml"))
    findings: list[dict[str, Any]] = []
    caveats: list[str] = []

    if semgrep_path is None:
        caveats.append("semgrep binary not found on PATH; findings unavailable")
    elif not rules_available:
        caveats.append(f"no rules under {_RULES_DIR}")
    else:
        # Restrict the scan to the lynchpin repo unless an explicit alternative
        # is provided. The privacy rule pack is calibrated to lynchpin paths.
        target = str((repo_root or Path.cwd()).resolve())
        findings = _run_semgrep(
            semgrep_path=semgrep_path,
            rules_dir=_RULES_DIR,
            target=target,
            recently_changed=recently_changed,
            project_root_map=snapshot_projects,
        )

    project_set = sorted({f["project"] for f in findings})
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "source": "semgrep --json with curated lynchpin-privacy rules",
            "scope": "lynchpin repo only; rule pack is calibrated to lynchpin paths",
            "promotion": "findings flagged recently_changed when matched path appears "
                         "in active_file_change_facts",
            "caveats": [
                "semgrep matches are heuristic; review each finding before acting",
                "rules are intentionally narrow — not a substitute for code review",
            ],
        },
        "inputs": {
            "rules_dir": str(_RULES_DIR),
            "rules": rules_available,
            "active_file_change_facts": str(file_changes_file or "active_file_change_facts.json"),
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
        },
        "projects": project_set,
        "findings": findings,
        "caveats": caveats,
    }


def run_active_semantic_static_findings(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    payload = build_active_semantic_static_findings(
        start=start, end=end, projects=projects,
        snapshot_file=snapshot_file, file_changes_file=file_changes_file,
        repo_root=repo_root,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


# ── plumbing ─────────────────────────────────────────────────────────────────


def _which_semgrep() -> str | None:
    import shutil
    return shutil.which("semgrep")


def _run_semgrep(
    *,
    semgrep_path: str,
    rules_dir: Path,
    target: str,
    recently_changed: dict[str, set[str]],
    project_root_map: dict[str, str],
) -> list[dict[str, Any]]:
    cmd = [
        semgrep_path, "scan",
        "--config", str(rules_dir),
        "--json",
        "--quiet",
        "--no-git-ignore",
        "--metrics", "off",
        "--disable-version-check",
        target,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not result.stdout:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return []

    findings: list[dict[str, Any]] = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        rule_id = str(r.get("check_id", ""))
        path = str(r.get("path", ""))
        start_info = r.get("start") or {}
        line = start_info.get("line") if isinstance(start_info, dict) else None
        try:
            line = int(line) if line is not None else 0
        except (TypeError, ValueError):
            line = 0
        extra = r.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        message = str(extra.get("message", ""))
        severity = str(extra.get("severity", "WARNING")).lower()

        project, rel_path = _resolve_project(path, project_root_map)
        is_recent = _is_recently_changed(project, rel_path, recently_changed)
        findings.append({
            "project": project,
            "rule_id": rule_id,
            "severity": severity,
            "path": rel_path,
            "line": line,
            "message": message[:600],
            "recently_changed": is_recent,
            "caveats": ["semgrep match requires human interpretation"],
        })
    return findings


def _resolve_project(path: str, project_root_map: dict[str, str]) -> tuple[str, str]:
    """Map an absolute path back to ``(project_name, relative_path)``."""
    abs_path = str(Path(path).resolve())
    for name, root in project_root_map.items():
        prefix = str(Path(root).resolve()) + "/"
        if abs_path.startswith(prefix):
            return name, abs_path[len(prefix):]
    # Fallback: caller is the lynchpin repo itself.
    return "sinity-lynchpin", path


def _is_recently_changed(
    project: str, path: str, recently_changed: dict[str, set[str]],
) -> bool:
    paths = recently_changed.get(project)
    if paths is None:
        return False
    return path in paths or path.lstrip("/") in paths


def _project_map(payload: dict[str, Any] | None, selected: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not payload:
        return out
    for row in payload.get("projects", []) or []:
        if not isinstance(row, dict):
            continue
        name = row.get("project")
        path = row.get("path")
        if not isinstance(name, str) or not isinstance(path, str):
            continue
        if selected and name not in selected:
            continue
        out[name] = path
    return out


def _changed_paths(
    payload: dict[str, Any] | None, selected: set[str],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if not payload:
        return result
    for row in payload.get("file_changes", []) or []:
        if not isinstance(row, dict):
            continue
        name = row.get("project")
        path = row.get("path")
        if not isinstance(name, str) or not isinstance(path, str):
            continue
        if selected and name not in selected:
            continue
        result.setdefault(name, set()).add(path)
    return result
