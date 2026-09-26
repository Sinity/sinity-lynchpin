"""Role-aware metrics over Chisel's captured file inventory.

Only captured files classified as implementation, tests, or tooling are sent to
Tokei. Documentation and project context therefore cannot inflate maintained
code totals, even when they contain fenced source examples.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

CODE_ROLES = ("implementation", "tests", "tooling")
ALL_ROLES = (
    "implementation",
    "tests",
    "tooling",
    "documentation",
    "context",
    "evidence",
    "unclassified",
)


def _get(record: Any, key: str, default: Any = None) -> Any:
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def _inventory_fields(inventory: Any) -> tuple[Path, str, str, list[Any]]:
    root = Path(_get(inventory, "root"))
    project = str(_get(inventory, "project", "project"))
    generated_at = str(_get(inventory, "generated_at", ""))
    return root, project, generated_at, list(_get(inventory, "files", ()))


def _relative_path(record: Any) -> str:
    return Path(str(_get(record, "path", ""))).as_posix().removeprefix("./")


def _tokei_stats(root: Path, paths: list[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    if not paths:
        return {}, None
    executable = shutil.which("tokei")
    if executable is None:
        return {}, "tokei_not_installed"
    by_path: dict[str, dict[str, Any]] = {}
    for start in range(0, len(paths), 512):
        chunk = paths[start : start + 512]
        result = subprocess.run(
            [executable, "--files", "--output", "json", "--no-ignore", "--", *chunk],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            return {}, f"tokei_failed: {(result.stderr or result.stdout).strip()}"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return {}, f"tokei_invalid_json: {exc}"
        for language, language_stats in payload.items():
            if language == "Total":
                continue
            for row in language_stats.get("reports") or ():
                report_path = Path(str(row.get("name", "")))
                try:
                    name = report_path.resolve().relative_to(root.resolve()).as_posix()
                except (OSError, ValueError):
                    name = report_path.as_posix().removeprefix("./")
                stats = row.get("stats") or {}
                # Deliberately use only physical-file stats. Tokei's embedded blobs
                # (for example fenced code in Markdown) are not maintained source.
                by_path[name] = {
                    "language": language,
                    "code": int(stats.get("code") or 0),
                    "comments": int(stats.get("comments") or 0),
                    "blanks": int(stats.get("blanks") or 0),
                    "lines": sum(int(stats.get(key) or 0) for key in ("code", "comments", "blanks")),
                }
    return by_path, None


def build_metrics(inventory: Any, package_dir: Path) -> dict[str, Any]:
    """Write detailed and summary metrics for one immutable captured inventory.

    Inventory records must carry ``path``, ``role``, ``included``, ``sha256``
    and ``size_bytes``. Included captured files are read from ``inventory.root``
    and hash-checked before measurement.
    """
    root, project, generated_at, records = _inventory_fields(inventory)
    rows: list[dict[str, Any]] = []
    candidates: list[str] = []
    coverage: list[str] = []
    from .chisel import _loc_policy_ignores, _read_loc_ignore_rules

    loc_ignore_rules = _read_loc_ignore_rules(root)
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {key: 0 for key in ("files", "bytes", "code", "comments", "blanks", "lines")}
    )
    for record in records:
        path = _relative_path(record)
        role = str(_get(record, "role", "unclassified"))
        if role not in ALL_ROLES:
            role = "unclassified"
        included = bool(_get(record, "included", False))
        full_path = root / path
        size = int(_get(record, "size_bytes", 0) or 0)
        digest = str(_get(record, "sha256", ""))
        row: dict[str, Any] = {
            "path": path,
            "role": role,
            "included": included,
            "sha256": digest,
            "size_bytes": size,
            "exclusion_reason": str(
                _get(record, "exclusion_reason", "")
                or ",".join(_get(record, "excluded_by", ()) or ())
            ),
            "metric_excluded_reason": "",
            "language": "",
            "code": None,
            "comments": None,
            "blanks": None,
            "lines": None,
        }
        if not included:
            row["exclusion_reason"] = row["exclusion_reason"] or "excluded_by_inventory"
        elif not full_path.is_file():
            raise ValueError(f"captured inventory file missing: {path}")
        else:
            content = full_path.read_bytes()
            actual_hash = hashlib.sha256(content).hexdigest()
            if digest and actual_hash != digest:
                raise ValueError(f"captured inventory hash mismatch: {path}")
            size = len(content)
            row["size_bytes"] = size
            totals[role]["files"] += 1
            totals[role]["bytes"] += size
            if role in CODE_ROLES and _loc_policy_ignores(path, loc_ignore_rules):
                row["metric_excluded_reason"] = "metric_ignore"
            elif role in CODE_ROLES:
                candidates.append(path)
        rows.append(row)

    parsed, parser_issue = _tokei_stats(root, candidates)
    if parser_issue:
        coverage.append(parser_issue)
    measured_paths = set(parsed)
    missing_results = {
        row["path"]
        for row in rows
        if row["role"] in CODE_ROLES
        and row["included"]
        and not row["exclusion_reason"]
        and not row["metric_excluded_reason"]
        and row["path"] not in measured_paths
    }
    if parser_issue is None:
        for row in rows:
            if row["role"] not in CODE_ROLES or not row["included"] or row["exclusion_reason"] or row["metric_excluded_reason"]:
                continue
            measured = parsed.get(row["path"])
            if measured is None:
                coverage.append(f"tokei_missing_file_result:{row['path']}")
                continue
            row.update(measured)
            total = totals[row["role"]]
            for key in ("code", "comments", "blanks", "lines"):
                total[key] += measured[key]

    # Unknowns remain visible as gaps and contribute bytes, never maintained LOC.
    has_unclassified = False
    for row in rows:
        if row["role"] == "unclassified" and row["included"]:
            has_unclassified = True
            coverage.append(f"unclassified_file:{row['path']}")
    roles = []
    for role in ALL_ROLES:
        value = totals[role]
        role_missing = any(
            row["role"] == role and row["path"] in missing_results for row in rows
        )
        loc_measured = role in CODE_ROLES and parser_issue is None and not role_missing
        roles.append(
            {
                "role": role,
                **{
                    **value,
                    **(
                        {}
                        if loc_measured
                        else {key: None for key in ("code", "comments", "blanks", "lines")}
                    ),
                },
                "loc_measured": loc_measured,
            }
        )
    code_sum = sum(totals[role]["code"] for role in CODE_ROLES)
    summary = {
        "schema_version": 1,
        "project": project,
        "generated_at": generated_at,
        "inventory_root": "source/",
        "files": len(rows),
        "included_files": sum(bool(row["included"]) and not row["exclusion_reason"] for row in rows),
        "known_maintained_code_lines": code_sum if parser_issue is None and not missing_results else None,
        "maintained_code_lines": code_sum if parser_issue is None and not missing_results and not has_unclassified else None,
        "maintained_code_coverage_complete": parser_issue is None and not missing_results and not has_unclassified,
        "maintained_roles": list(CODE_ROLES),
        "roles": roles,
        "coverage_gaps": sorted(set(coverage)),
        "interpretation": {
            "test_source_share": None,
            "test_source_share_note": "Test source LOC is not coverage and is not presented as a coverage measure.",
            "rust_inline_tests": "Included within their physical implementation file; any separately reported inline subset is non-additive.",
        },
    }
    metrics_dir = package_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    with (metrics_dir / "files.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["path", "role", "included", "sha256", "size_bytes", "exclusion_reason", "language", "code", "comments", "blanks", "lines"])
        writer.writeheader()
        writer.writerows(rows)
    with (metrics_dir / "files.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    with (metrics_dir / "roles.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["role", "files", "bytes", "code", "comments", "blanks", "lines", "loc_measured"])
        writer.writeheader()
        writer.writerows(roles)
    (metrics_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    loc_by_role = {entry["role"]: entry["loc_measured"] for entry in roles}
    legacy_buckets = {
        role: _bucket(totals[role], loc_measured=loc_by_role[role])
        for role in ALL_ROLES
    }
    rust_inline_tests, rust_split_test_files = _rust_test_subsets(root, project, rows)
    legacy = {
        "project": project,
        "generated_at": generated_at,
        "source": "source/",
        "input_policy": "captured-inventory-role-policy",
        "input_files": summary["included_files"],
        "buckets": legacy_buckets,
        "files": rows,
        "rust_inline_tests": rust_inline_tests,
        "rust_split_test_files": rust_split_test_files,
        "coverage_gaps": summary["coverage_gaps"],
    }
    (package_dir / f"{project}-tokei-stats.json").write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = [f"# {project} role-aware source metrics", "", f"Generated: {generated_at}", "", "LOC includes only implementation, tests, and tooling files measured from the captured inventory. Documentation and project context bytes are reported separately; their prose and code fences do not enter maintained LOC.", "", "| Role | Files | Bytes | Code | Comments | Blanks |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    md.extend(f"| {r['role']} | {r['files']} | {r['bytes']} | {r['code'] if r['loc_measured'] else 'unavailable'} | {r['comments'] if r['loc_measured'] else 'unavailable'} | {r['blanks'] if r['loc_measured'] else 'unavailable'} |" for r in roles)
    if summary["coverage_gaps"]:
        md.extend(("", "## Coverage gaps", "", *[f"- {gap}" for gap in summary["coverage_gaps"]]))
    (package_dir / f"{project}-tokei-stats.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return summary


def _rust_test_subsets(
    root: Path,
    project: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .chisel import RepoPlan, _rust_inline_test_stats, _rust_split_test_file_stats

    inline_paths = {
        row["path"]
        for row in rows
        if row["role"] == "implementation"
        and row["path"].endswith(".rs")
        and row["included"]
        and not row["exclusion_reason"]
        and not row["metric_excluded_reason"]
    }
    split_paths = {
        row["path"]
        for row in rows
        if row["role"] == "tests"
        and row["path"].endswith(".rs")
        and row["included"]
        and not row["exclusion_reason"]
        and not row["metric_excluded_reason"]
    }
    captured_plan = RepoPlan(name=project, path=root, slices=())
    inline = _rust_inline_test_stats(captured_plan, inline_paths)
    split = _rust_split_test_file_stats(captured_plan, split_paths)
    inline["available"] = True
    inline["note"] = (
        "Inline Rust test lines are a heuristic subset of implementation files; "
        "they are already included in implementation LOC and must not be added."
    )
    split["available"] = True
    split["note"] = (
        "Split Rust test files are in the tests role and must not be added again. "
        "The line count is physical lines, not Tokei code/comment classification."
    )
    return inline, split


def _bucket(value: dict[str, int], *, loc_measured: bool) -> dict[str, Any]:
    return {
        "files": value["files"],
        "bytes": value["bytes"],
        "code": value["code"] if loc_measured else None,
        "comments": value["comments"] if loc_measured else None,
        "blanks": value["blanks"] if loc_measured else None,
        "lines": value["lines"] if loc_measured else None,
        "loc_measured": loc_measured,
        "languages": {},
    }
