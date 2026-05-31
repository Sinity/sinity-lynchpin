"""GitHub Actions workflow health analysis.

Static mode (default): parses ``.github/workflows/*.yml`` per active project
and emits a structural inventory: workflow names, jobs, runners, declared
timeouts, and trigger events. No network calls.

Network mode (``include_runs=True``): additionally calls
``gh api repos/{owner}/{repo}/actions/runs`` for the last 30 days and
computes pass/fail counts plus duration percentiles per workflow.
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

import yaml

from ...core.parse import parse_datetime
from ...sources.github import repo_slug
from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


_WORKFLOW_GLOB = ".github/workflows"
_GH_TIMEOUT_S = 90
_RUNS_LOOKBACK_DAYS = 30
_RUNS_PER_WORKFLOW = 50


def build_active_ci_health(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    include_runs: bool = False,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot = load_json_object(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )
    selected = set(projects or ())
    snapshot_projects = _project_paths(snapshot, selected)

    gh_path = shutil.which("gh") if include_runs else None
    pack_caveats: list[str] = []
    if include_runs and gh_path is None:
        pack_caveats.append(
            "gh not found on PATH; check-run history skipped (static workflow inventory only)"
        )

    project_rows: list[dict[str, Any]] = []
    for project_name, root in sorted(snapshot_projects.items()):
        repo_path = Path(root)
        workflow_dir = repo_path / _WORKFLOW_GLOB
        if not workflow_dir.is_dir():
            continue
        workflows = []
        parse_errors: list[str] = []
        for wf_path in sorted(workflow_dir.glob("*.y*ml")):
            try:
                payload = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                parse_errors.append(f"{wf_path.name}: {type(exc).__name__}: {exc}")
                continue
            if not isinstance(payload, dict):
                continue
            workflows.append(_summarize_workflow(wf_path, payload))

        runs_block: dict[str, Any] | None = None
        if include_runs and gh_path is not None:
            slug = repo_slug(repo_path)
            if slug is None:
                runs_block = {"available": False, "reason": "no GitHub origin remote"}
            else:
                runs_block = _fetch_runs_summary(
                    gh_path=gh_path, slug=slug,
                    repo_path=repo_path, workflows=workflows, end=end,
                )

        project_rows.append({
            "project": project_name,
            "workflow_count": len(workflows),
            "total_job_count": sum(len(wf.get("jobs", [])) for wf in workflows),
            "explicit_timeout_count": sum(
                1 for wf in workflows for job in wf.get("jobs", []) if job.get("timeout_minutes") is not None
            ),
            "missing_timeout_count": sum(
                1 for wf in workflows for job in wf.get("jobs", []) if job.get("timeout_minutes") is None
            ),
            "workflows": workflows,
            "parse_errors": parse_errors,
            "runs": runs_block,
        })

    if not project_rows:
        pack_caveats.append("no .github/workflows directories found in active project checkouts")

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "scope": "static .github/workflows/*.y(a)ml parsing"
                     + (" + gh api runs telemetry" if include_runs else " — no network calls"),
            "timeouts": "jobs report explicit timeout-minutes; missing_timeout_count flags jobs that "
                        "rely on the GitHub default (360 minutes)",
            "runs": (
                f"gh api repos/{{slug}}/actions/runs over last {_RUNS_LOOKBACK_DAYS} days, "
                f"capped at {_RUNS_PER_WORKFLOW} per workflow; durations are run_duration_ms"
                if include_runs else "disabled"
            ),
        },
        "projects": project_rows,
        "caveats": pack_caveats,
    }


def run_active_ci_health(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    include_runs: bool = False,
) -> dict[str, Any]:
    payload = build_active_ci_health(
        start=start, end=end, projects=projects, snapshot_file=snapshot_file,
        include_runs=include_runs,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _fetch_runs_summary(
    *,
    gh_path: str,
    slug: str,
    repo_path: Path,
    workflows: list[dict[str, Any]],
    end: date,
) -> dict[str, Any]:
    cutoff = end - timedelta(days=_RUNS_LOOKBACK_DAYS)
    workflow_summaries: list[dict[str, Any]] = []
    fetch_errors: list[str] = []
    total_runs = 0
    for wf in workflows:
        wf_path = wf.get("path") or ""
        wf_runs, error = _fetch_workflow_runs(
            gh_path=gh_path, slug=slug, workflow_path=wf_path,
            cutoff=cutoff, repo_path=repo_path,
        )
        if error:
            fetch_errors.append(f"{wf_path}: {error}")
        if not wf_runs:
            workflow_summaries.append({
                "name": wf.get("name"),
                "path": wf.get("path"),
                "run_count": 0,
            })
            continue
        success = sum(1 for r in wf_runs if r.get("conclusion") == "success")
        failure = sum(1 for r in wf_runs if r.get("conclusion") == "failure")
        cancelled = sum(1 for r in wf_runs if r.get("conclusion") == "cancelled")
        skipped = sum(1 for r in wf_runs if r.get("conclusion") == "skipped")
        durations = [d for d in (_run_duration_seconds(r) for r in wf_runs) if d > 0]
        durations.sort()
        recent_failures = [
            {
                "id": r.get("id"),
                "conclusion": r.get("conclusion"),
                "created_at": r.get("created_at"),
                "head_branch": r.get("head_branch"),
                "url": r.get("html_url"),
            }
            for r in wf_runs if r.get("conclusion") == "failure"
        ][:5]
        flaky = success > 0 and failure > 0 and failure / max(success + failure, 1) >= 0.2
        total_runs += len(wf_runs)
        workflow_summaries.append({
            "name": wf.get("name"),
            "path": wf.get("path"),
            "run_count": len(wf_runs),
            "success_count": success,
            "failure_count": failure,
            "cancelled_count": cancelled,
            "skipped_count": skipped,
            "success_rate": round(success / max(success + failure, 1), 3),
            "p50_duration_s": round(_percentile(durations, 0.50), 1) if durations else None,
            "p90_duration_s": round(_percentile(durations, 0.90), 1) if durations else None,
            "max_duration_s": round(durations[-1], 1) if durations else None,
            "flaky": flaky,
            "recent_failures": recent_failures,
        })

    return {
        "available": True,
        "slug": slug,
        "lookback_days": _RUNS_LOOKBACK_DAYS,
        "total_run_count": total_runs,
        "fetch_errors": fetch_errors,
        "workflows": workflow_summaries,
    }


def _fetch_workflow_runs(
    *,
    gh_path: str,
    slug: str,
    workflow_path: str,
    cutoff: date,
    repo_path: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    if not workflow_path:
        return [], "missing workflow path"
    cmd = [
        gh_path, "api",
        f"repos/{slug}/actions/workflows/{workflow_path}/runs"
        f"?per_page={_RUNS_PER_WORKFLOW}&created=>=" + cutoff.isoformat(),
        "--jq", ".workflow_runs",
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(repo_path),
            capture_output=True, text=True, timeout=_GH_TIMEOUT_S, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return [], f"gh api exited {result.returncode}: {result.stderr[:200]}"
    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"json parse failed: {exc}"
    if not isinstance(runs, list):
        return [], "unexpected payload shape"
    return runs, None


def _run_duration_seconds(run: dict[str, Any]) -> float:
    direct = run.get("run_duration_ms")
    if isinstance(direct, (int, float)) and direct > 0:
        return float(direct) / 1000.0
    started = _parse_iso(run.get("run_started_at") or run.get("created_at"))
    finished = _parse_iso(run.get("updated_at"))
    if started and finished and finished >= started:
        return (finished - started).total_seconds()
    return 0.0


def _parse_iso(value: object) -> datetime | None:
    return parse_datetime(value) if isinstance(value, str) and value else None


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _summarize_workflow(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name") or path.stem
    triggers = _normalize_triggers(payload.get("on") or payload.get(True))
    jobs_payload = payload.get("jobs") or {}
    jobs: list[dict[str, Any]] = []
    if isinstance(jobs_payload, dict):
        for job_id, job in jobs_payload.items():
            if not isinstance(job, dict):
                continue
            timeout = job.get("timeout-minutes")
            try:
                timeout_value: int | None = int(timeout) if timeout is not None else None
            except (TypeError, ValueError):
                timeout_value = None
            runs_on = job.get("runs-on")
            if isinstance(runs_on, list):
                runner = ",".join(str(r) for r in runs_on)
            elif runs_on is None:
                runner = None
            else:
                runner = str(runs_on)
            steps = job.get("steps")
            step_count = len(steps) if isinstance(steps, list) else 0
            jobs.append({
                "id": str(job_id),
                "name": str(job.get("name") or job_id),
                "runs_on": runner,
                "timeout_minutes": timeout_value,
                "step_count": step_count,
                "needs": _string_list(job.get("needs")),
            })
    return {
        "name": str(name),
        "path": path.name,
        "triggers": triggers,
        "jobs": jobs,
    }


def _normalize_triggers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    return []


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _project_paths(snapshot: dict[str, Any], selected: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = snapshot.get("projects")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        if path:
            out[project] = path
    return out


__all__ = ["build_active_ci_health", "run_active_ci_health"]
