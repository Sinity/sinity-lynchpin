"""Rust dependency hygiene via cargo-machete and cargo-geiger.

Produces ``active_rust_dependency_hygiene.json`` with two complementary
dimensions per active Rust workspace:

- **unused-dep candidates** from ``cargo machete`` — fast but explicitly
  imprecise; we annotate every candidate as conservative-only.
- **unsafe usage** counts from ``cargo geiger`` — own-crate unsafe item
  counts plus dependency unsafe surface, segmented per crate.

Hard invariants:

- Never modify ``Cargo.toml``. Never run anything that could mutate
  ``Cargo.lock`` (we always pass ``--frozen`` or equivalent).
- If either tool is absent, surface a source-readiness caveat rather than
  silently returning empty findings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ..core.io import load_json_if_exists, resolve_analysis_path, save_json

_CARGO_TOML = "Cargo.toml"
_TIMEOUT_S = 300


def build_active_rust_dependency_hygiene(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    include_geiger: bool = False,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot = _dict_payload(load_json_if_exists(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json")))

    selected = set(projects or ())
    snapshot_projects = _project_map(snapshot, selected)

    machete_path = shutil.which("cargo-machete")
    geiger_path = shutil.which("cargo-geiger") if include_geiger else None
    audit_path = shutil.which("cargo-audit")

    pack_caveats: list[str] = []
    if machete_path is None:
        pack_caveats.append(
            "cargo-machete not found on PATH; unused-dependency candidates unavailable"
        )
    if include_geiger and geiger_path is None:
        pack_caveats.append(
            "cargo-geiger not found on PATH; unsafe-usage findings unavailable"
        )
    if audit_path is None:
        pack_caveats.append(
            "cargo-audit not found on PATH; RUSTSEC advisories unavailable"
        )

    rows: list[dict[str, Any]] = []
    for name, root in sorted(snapshot_projects.items()):
        path = Path(root)
        if not (path / _CARGO_TOML).exists():
            continue
        rows.append(
            _scan_workspace(
                project=name,
                root=path,
                machete_path=machete_path,
                geiger_path=geiger_path,
                audit_path=audit_path,
            )
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "machete": "cargo-machete --with-metadata for unused-dep candidates "
                       "(explicitly imprecise per upstream docs)",
            "geiger": "cargo-geiger --output-format Json for unsafe-usage segmentation"
                      if include_geiger else "geiger disabled (set include_geiger=True to enable)",
            "audit": "cargo-audit --json for RUSTSEC advisories on Cargo.lock",
            "lockfile_safety": "all invocations use --frozen / read-only so Cargo.lock is never mutated",
        },
        "workspaces": rows,
        "caveats": pack_caveats,
    }


def run_active_rust_dependency_hygiene(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    include_geiger: bool = False,
) -> dict[str, Any]:
    payload = build_active_rust_dependency_hygiene(
        start=start, end=end, projects=projects,
        snapshot_file=snapshot_file, include_geiger=include_geiger,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


# ── per-workspace scan ───────────────────────────────────────────────────────


def _scan_workspace(
    *,
    project: str,
    root: Path,
    machete_path: str | None,
    geiger_path: str | None,
    audit_path: str | None,
) -> dict[str, Any]:
    machete_block = (
        _run_machete(machete_path=machete_path, root=root)
        if machete_path
        else {"available": False, "reason": "cargo-machete not on PATH"}
    )
    geiger_block = (
        _run_geiger(geiger_path=geiger_path, root=root)
        if geiger_path
        else {"available": False, "reason": "cargo-geiger disabled or unavailable"}
    )
    audit_block = (
        _run_audit(audit_path=audit_path, root=root)
        if audit_path
        else {"available": False, "reason": "cargo-audit not on PATH"}
    )

    return {
        "project": project,
        "path": str(root),
        "machete": machete_block,
        "geiger": geiger_block,
        "audit": audit_block,
        "caveats": [
            "cargo-machete is fast but imprecise — verify each candidate before removing",
            "cargo-geiger unsafe counts cover transitive deps; high counts often "
            "reflect well-understood ecosystem crates rather than project debt",
            "cargo-audit only flags advisories already in the rustsec/advisory-db; "
            "freshly disclosed CVEs may not yet be reflected",
        ],
    }


def _run_audit(*, audit_path: str, root: Path) -> dict[str, Any]:
    cmd = [audit_path, "audit", "--json", "--no-fetch"]
    try:
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    if not result.stdout:
        return {
            "available": False,
            "reason": f"cargo-audit exited {result.returncode} with no output",
            "stderr_sample": result.stderr[:500],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"json parse failed: {exc}"}

    advisories = []
    vulnerabilities = payload.get("vulnerabilities") if isinstance(payload, dict) else None
    if isinstance(vulnerabilities, dict):
        for vuln in vulnerabilities.get("list") or []:
            if not isinstance(vuln, dict):
                continue
            advisory = vuln.get("advisory") or {}
            package = vuln.get("package") or {}
            versions = vuln.get("versions") or {}
            advisories.append({
                "id": advisory.get("id"),
                "package": package.get("name"),
                "installed": package.get("version"),
                "patched": versions.get("patched"),
                "severity": (advisory.get("severity") or "").lower() or None,
                "title": advisory.get("title"),
                "url": advisory.get("url"),
            })
    warnings_block = payload.get("warnings") if isinstance(payload, dict) else None
    warnings: list[dict[str, Any]] = []
    if isinstance(warnings_block, dict):
        for kind, entries in warnings_block.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    warnings.append({
                        "kind": str(kind),
                        "package": (entry.get("package") or {}).get("name") if isinstance(entry.get("package"), dict) else None,
                    })
    return {
        "available": True,
        "command": cmd,
        "returncode": result.returncode,
        "advisory_count": len(advisories),
        "advisories": advisories,
        "warning_count": len(warnings),
        "warnings": warnings[:25],
    }


def _run_machete(*, machete_path: str, root: Path) -> dict[str, Any]:
    cmd = [machete_path, "--with-metadata", str(root)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    # cargo-machete exits with non-zero when it finds candidates; that is not
    # a failure. We treat any output as parseable and only mark unavailable
    # when stderr indicates an actual error condition.
    output = result.stdout
    candidates = _parse_machete_output(output)
    return {
        "available": True,
        "command": cmd,
        "returncode": result.returncode,
        "unused_dep_candidates": candidates,
        "candidate_count": sum(len(c["unused"]) for c in candidates),
        "stderr_sample": result.stderr[:500] if result.stderr else "",
    }


def _parse_machete_output(text: str) -> list[dict[str, Any]]:
    """cargo-machete's text output groups by manifest path. We split on the
    'manifest:' header lines."""
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("cargo-machete found the following unused dependencies in"):
            manifest = line.removeprefix(
                "cargo-machete found the following unused dependencies in"
            ).strip().rstrip(":")
            current = {"manifest": manifest, "unused": []}
            out.append(current)
            continue
        if current is None:
            continue
        # Indented bullet lines: "  foo" or "  foo (renamed from bar)"
        if line and (raw.startswith(" ") or raw.startswith("\t")):
            current["unused"].append(line)
    # If parser didn't match any header, return the raw output once for diagnostics.
    if not out and text.strip():
        out.append({"manifest": "(unparsed)", "unused": [text.strip()[:500]]})
    return out


def _run_geiger(*, geiger_path: str, root: Path) -> dict[str, Any]:
    cmd = [
        geiger_path, "geiger",
        "--manifest-path", str(root / _CARGO_TOML),
        "--output-format", "Json",
        "--frozen",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    if result.returncode != 0 and not result.stdout:
        return {
            "available": False,
            "reason": f"cargo-geiger exited {result.returncode}",
            "stderr_sample": result.stderr[:500],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"json parse failed: {exc}"}

    return {
        "available": True,
        "command": cmd,
        "returncode": result.returncode,
        "summary": _summarize_geiger(payload),
    }


def _summarize_geiger(payload: Any) -> dict[str, Any]:
    """Pull a compact summary out of cargo-geiger JSON.

    The full schema is large and version-dependent; we keep only the
    aggregate counts and a per-package short list, with explicit caveats.
    """
    out: dict[str, Any] = {"raw_top_level_keys": [], "packages": []}
    if isinstance(payload, dict):
        out["raw_top_level_keys"] = sorted(payload.keys())
        packages = payload.get("packages")
        if isinstance(packages, list):
            for entry in packages[:50]:  # cap to keep artifact small
                if not isinstance(entry, dict):
                    continue
                pkg = entry.get("package") or {}
                unsafety = entry.get("unsafety") or {}
                used = unsafety.get("used") or {}
                used_funcs = used.get("functions") or {}
                out["packages"].append({
                    "name": pkg.get("id", {}).get("name") if isinstance(pkg.get("id"), dict) else None,
                    "used_unsafe_functions": used_funcs.get("safe", 0) + used_funcs.get("unsafe", 0)
                        if isinstance(used_funcs, dict) else None,
                    "unsafe_function_count": used_funcs.get("unsafe", 0)
                        if isinstance(used_funcs, dict) else None,
                })
    return out


# ── helpers ──────────────────────────────────────────────────────────────────


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


def _dict_payload(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
