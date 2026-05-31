"""Tokei-backed per-project language inventory artefact."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any, Sequence

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


def _tokei_version() -> str | None:
    try:
        result = subprocess.run(
            ["tokei", "--version"], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def _run_tokei(project_path: Path) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["tokei", "-o", "json", str(project_path)],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _language_rows(raw: dict[str, Any]) -> dict[str, dict[str, int]]:
    langs: dict[str, dict[str, int]] = {}
    for lang, stats in raw.items():
        if lang == "Total" or not isinstance(stats, dict):
            continue
        code, comments, blanks = (
            int(stats.get(k, 0)) for k in ("code", "comments", "blanks")
        )
        if code + comments + blanks > 0:
            langs[lang] = {"code": code, "comments": comments, "blanks": blanks}
    return langs


def _empty_row(project: str, path: str, tool_available: bool, version: str | None,
               caveat: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "project": project, "path": path,
        "languages": {}, "total_lines": 0, "total_code_lines": 0,
        "dominant_languages": [],
        "tool_run": {"available": tool_available, "version": version, "returncode": None},
    }
    if caveat:
        row["caveat"] = caveat
    return row


def build_active_code_inventory(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str],
) -> dict[str, Any]:
    """Produce per-project tokei language breakdown from active_project_snapshot."""
    snapshot = load_json_object(snapshot_file, label="active project snapshot")
    snapshot_projects = snapshot.get("projects") or []

    version = _tokei_version()
    tool_available = version is not None
    project_set = set(projects) if projects else None
    rows: list[dict[str, Any]] = []

    for entry in snapshot_projects:
        if not isinstance(entry, dict):
            continue
        name = entry.get("project")
        path_str = entry.get("path")
        if not name or not path_str or (project_set and name not in project_set):
            continue
        project_path = Path(path_str)
        if not project_path.is_dir():
            rows.append(_empty_row(name, str(project_path), tool_available, version,
                                   caveat="project directory not found"))
            continue

        raw = _run_tokei(project_path) if tool_available else None
        if raw is None:
            rows.append(_empty_row(name, str(project_path), tool_available, version))
            continue

        langs = _language_rows(raw)
        total_code = sum(s["code"] for s in langs.values())
        total_lines = sum(s["code"] + s["comments"] + s["blanks"] for s in langs.values())
        dominant = sorted(langs, key=lambda k: -langs[k]["code"])[:4]

        rows.append({
            "project": name, "path": str(project_path),
            "languages": langs, "total_lines": total_lines,
            "total_code_lines": total_code,
            "dominant_languages": dominant,
            "tool_run": {"available": True, "version": version, "returncode": 0},
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None},
        "methodology": {
            "tool": "tokei (scc alternative)",
            "source": "active_project_snapshot project paths",
            "scope": "all files in project root not excluded by .gitignore",
        },
        "projects": rows,
    }


def run_active_code_inventory(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] = "active_project_snapshot.json",
) -> dict[str, Any]:
    """Materialize the active code inventory artefact."""
    payload = build_active_code_inventory(
        start=start, end=end, projects=projects, snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


__all__ = ["build_active_code_inventory", "run_active_code_inventory"]
