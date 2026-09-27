"""Tracker and owner-published verification context for Chisel packages.

This module only consumes materialized GitHub rows, the already generated Beads
export, and AgentCTL's public evidence route. It never runs project tests or
reads private verification caches.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from typing import Any

from . import agentctl
from .campaign import read_native_evidence
from .github_context import github_item_to_payload


def build_context(
    repo: Path,
    package_dir: Path,
    *,
    project: str,
    revision: str,
    dirty: bool | None = None,
    github_slug: str | None = None,
    github_items: Sequence[Any] = (),
) -> dict[str, Any]:
    """Write raw and readable tracker/verification evidence under ``trackers``.

    ``revision`` is the captured source revision. A dirty captured tree needs
    content comparison downstream before applicability can be decided. Missing
    owner fields remain null and are explained in the generated guide.
    """
    # Owner identity is read only from captured source; repo is used solely as
    # the working directory for GitHub's read-only Actions API client.
    root = package_dir / "trackers"
    from .chisel_options import active_options
    root.mkdir(parents=True, exist_ok=True)
    observed_at = datetime.now(timezone.utc).isoformat()

    github_rows = [
        github_item_to_payload(project=project, item=item) for item in github_items
    ]
    github_rows.sort(key=lambda row: (row.get("kind", ""), int(row.get("number") or 0)))
    _write_jsonl(root / "github.jsonl", github_rows)
    if active_options.xml:
        _write_github_markdown(root / "github.md", github_rows)

    beads_source = package_dir / f"{project}-beads-export.jsonl"
    beads_dest = root / "beads-export.jsonl"
    beads_count = sum(bool(line.strip()) for line in beads_dest.read_text().splitlines()) if beads_dest.exists() else _copy_jsonl(beads_source, beads_dest)
    _write_beads_index(root / "beads.md", beads_dest, beads_count)

    owner_id, owner_descriptor = _owner_project(package_dir / "source", project)
    evidence = read_native_evidence(owner_id)
    selected_revisions = {revision}
    catalogue = package_dir / "snapshots.json"
    if catalogue.is_file():
        selected_revisions.update(
            row["revision"]
            for row in json.loads(catalogue.read_text()).get("snapshots", [])
            if isinstance(row, dict) and isinstance(row.get("revision"), str)
        )
    jobs = _compact_job_details(
        _agentctl_jobs(owner_id, selected_revisions=selected_revisions),
        package_dir,
        revision,
    )
    from .campaign import read_batches
    from .beads import read_tasks

    roots = [root for name, root in active_options.task_roots if name == project]
    frozen = package_dir / "owners"
    frozen.mkdir(exist_ok=True)
    snapshots = {"native": evidence, "jobs": jobs}
    if roots:
        snapshots["tasks"] = read_tasks(owner_id, roots=roots, max_nodes=1000)
        snapshots["batches"] = read_batches(owner_id)
    for name, snapshot in snapshots.items():
        (frozen / f"{name}.json").write_text(json.dumps(snapshot, indent=2, default=str) + "\n")
    normalized = _verification_payload(
        evidence,
        jobs=jobs,
        revision=revision,
        dirty=dirty,
        observed_at=observed_at,
        package_project=project,
        owner_project=owner_id,
        owner_descriptor=owner_descriptor,
    )
    verification_root = package_dir / "verification"
    verification_root.mkdir(parents=True, exist_ok=True)
    from .chisel_execution import execution_snapshot, revision_checks

    if project == "sinex":
        execution = execution_snapshot(days=active_options.context_days)
        (verification_root / "sinex-execution.json").write_text(json.dumps(execution, indent=2, default=str) + "\n")
        for kind, records in execution["records"].items():
            _write_jsonl(verification_root / f"sinex-{kind}.jsonl", json.loads(json.dumps(records, default=str)))
    if active_options.refresh and github_slug:
        catalogue_path = package_dir / "snapshots.json"
        catalogue = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else {"snapshots": []}
        checks = revision_checks(repo, github_slug, [revision, *[row["revision"] for row in catalogue["snapshots"] if row.get("revision")]])
        (verification_root / "revision-checks.json").write_text(json.dumps(checks, indent=2) + "\n")
        _write_jsonl(verification_root / "revision-checks.jsonl", checks["records"])
    ci = read_ci_runs(
        repo,
        github_slug=github_slug,
        revision=revision,
        dirty=dirty,
    ) if active_options.refresh else {"records": [], "coverage": {"coverage": "unavailable", "reason": "network refresh not requested", "revision": revision, "record_count": 0, "window_start": None, "window_end": None, "pages_read": 0, "capped": False, "gaps": ["network refresh not requested"]}}
    normalized["coverage_document"]["hosted_ci_run_exports"] = ci["coverage"][
        "coverage"
    ]
    normalized["coverage_document"]["hosted_ci_owner_route"] = ci["coverage"]
    _write_jsonl(
        verification_root / "records.jsonl",
        [
            *({"kind": "native_evidence", **row} for row in normalized["records"]),
            *(
                {"kind": "agentctl_job_observation", **row}
                for row in normalized["job_observations"]
            ),
        ],
    )
    (verification_root / "coverage.json").write_text(
        json.dumps(
            normalized["coverage_document"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_jsonl(verification_root / "ci-runs.jsonl", ci["records"])
    (verification_root / "ci-coverage.json").write_text(
        json.dumps(ci["coverage"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_verification_markdown(verification_root / "README.md", normalized)
    _write_ci_markdown(verification_root / "ci-runs.md", ci)

    return {
        "github_items": len(github_rows),
        "github_prs": sum(row.get("kind") == "pr" for row in github_rows),
        "beads_records": beads_count,
        "verification_records": len(normalized["records"]),
        "verification_coverage": normalized["coverage"],
        "verification_jobs": len(normalized["job_observations"]),
        "owner_project": owner_id,
        "files": [name for name in [
            "trackers/github.jsonl",
            "trackers/github.md",
            "trackers/beads-export.jsonl",
            "trackers/beads.md",
            "verification/records.jsonl",
            "verification/coverage.json",
            "verification/README.md",
            "verification/ci-runs.jsonl",
            "verification/ci-coverage.json",
            "verification/ci-runs.md",
        ] if (package_dir / name).exists()],
    }


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _copy_jsonl(source: Path, destination: Path) -> int:
    if not source.is_file():
        destination.write_text("", encoding="utf-8")
        return 0
    count = 0
    with (
        source.open(encoding="utf-8") as src,
        destination.open("w", encoding="utf-8") as dst,
    ):
        for line in src:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                # Preserve owner bytes for forensic fidelity; downstream readers
                # can identify the malformed source line themselves.
                dst.write(line if line.endswith("\n") else line + "\n")
            else:
                dst.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _write_github_markdown(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    lines = ["# GitHub issues and pull requests", ""]
    for row in rows:
        kind = str(row.get("kind", "item")).upper()
        state = str(row.get("state") or "unknown")
        link = row.get("url")
        title = str(row.get("title") or "(untitled)").replace("\n", " ")
        heading = f"## {kind} #{row.get('number', '?')} [{state}] {title}"
        lines.extend([heading, "", f"- Author: {row.get('author') or 'unknown'}"])
        lines.append(f"- Created: {row.get('created_at') or 'unknown'}")
        lines.append(f"- Updated: {row.get('updated_at') or 'unknown'}")
        if row.get("closed_at"):
            lines.append(f"- Closed: {row['closed_at']}")
        if row.get("merged_at"):
            lines.append(f"- Merged: {row['merged_at']}")
        if link:
            lines.append(f"- URL: {link}")
        labels = row.get("labels") or []
        if labels:
            lines.append("- Labels: " + ", ".join(str(label) for label in labels))
        lines.extend(["", str(row.get("body") or "(no body)"), ""])
        for comment in row.get("comments") or []:
            lines.extend(
                [
                    f"### Comment by {comment.get('author') or 'unknown'} ({comment.get('created_at') or 'date unknown'})",
                    "",
                    str(comment.get("body") or ""),
                    "",
                ]
            )
        for review in row.get("reviews") or []:
            lines.extend(
                [
                    f"### Review by {review.get('author') or 'unknown'}: {review.get('state') or 'state unknown'} ({review.get('submitted_at') or 'date unknown'})",
                    "",
                    str(review.get("body") or ""),
                    "",
                ]
            )
        for comment in row.get("review_comments") or []:
            location = comment.get("path") or "path unknown"
            if comment.get("line") is not None:
                location += f":{comment['line']}"
            lines.extend(
                [
                    f"### Inline review by {comment.get('author') or 'unknown'} at `{location}`",
                    "",
                    str(comment.get("body") or ""),
                    "",
                ]
            )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_beads_index(path: Path, export: Path, count: int) -> None:
    status = (
        f"{count} records copied from the Chisel Beads export"
        if count
        else "No generated Beads export was available."
    )
    path.write_text(
        "# Beads task and memory records\n\n"
        f"{status}\n\n"
        "The adjacent `beads-export.jsonl` preserves the existing `bd export "
        "--include-memories` records. This package step does not query or mutate "
        "the Beads owner. Read the JSONL for complete issue, memory, and dependency fields.\n",
        encoding="utf-8",
    )


def _verification_payload(
    evidence: dict[str, Any],
    *,
    jobs: dict[str, Any],
    revision: str,
    dirty: bool | None,
    observed_at: str,
    package_project: str,
    owner_project: str,
    owner_descriptor: dict[str, Any],
) -> dict[str, Any]:
    records = []
    for row in evidence.get("rows", []):
        if not isinstance(row, dict):
            continue
        candidate = (
            row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
        )
        result = (
            row.get("worker_result")
            if isinstance(row.get("worker_result"), dict)
            else {}
        )
        tested_revision = (
            candidate.get("candidate_sha")
            or result.get("candidate_sha")
            or candidate.get("commit")
            or candidate.get("revision")
            or result.get("tested_revision")
        )
        owner_dirty = candidate.get("current_dirty", candidate.get("dirty"))
        checks = (
            row.get("verification") if isinstance(row.get("verification"), list) else []
        )
        normalized_checks = []
        for check in checks:
            check = check if isinstance(check, dict) else {}
            claim = (
                check.get("claim") if isinstance(check.get("claim"), dict) else check
            )
            observation = (
                check.get("observation")
                if isinstance(check.get("observation"), dict)
                else {}
            )
            tested_sha = (
                claim.get("tested_sha") or claim.get("revision") or tested_revision
            )
            receipt_observed = observation.get("checked") is True
            owner_result = {
                "phase": observation.get("phase"),
                "exit_code": observation.get("exit_code"),
                "eligible": observation.get("eligible"),
                "result_kind": observation.get("result_kind"),
                "execution_receipt": observation.get("execution_receipt"),
                "execution_evidence": observation.get("execution_evidence"),
            }
            normalized_checks.append(
                {
                    "command": claim.get("command"),
                    "argv": observation.get("argv"),
                    "selection": (claim.get("coverage") or {}).get("scope")
                    if isinstance(claim.get("coverage"), dict)
                    else (claim.get("selection") or claim.get("scope")),
                    "criterion_ids": (claim.get("coverage") or {}).get("ac_ids")
                    if isinstance(claim.get("coverage"), dict)
                    else None,
                    "timestamp": observation.get("observed_at")
                    or row.get("recorded_at"),
                    "environment": observation.get("environment"),
                    "claimed_outcome": claim.get("status") or claim.get("outcome"),
                    "owner_observation": owner_result,
                    "receipt": claim.get("receipt"),
                    "tested_revision": tested_sha,
                    "dirty": owner_dirty,
                    "applicable_to_package": (
                        tested_sha == revision and receipt_observed
                        and observation.get("eligible") is True
                    ) if dirty is False else None if tested_sha == revision else False,
                }
            )
        records.append(
            {
                "evidence_id": row.get("evidence_id"),
                "recorded_at": row.get("recorded_at"),
                "candidate_revision": tested_revision,
                "candidate_dirty": owner_dirty,
                "verification": normalized_checks,
                "source_record": row,
            }
        )
    gaps = (
        list(evidence.get("gaps") or [])
        + list(jobs.get("gaps") or [])
        + [
            "Configured test, benchmark, coverage, or CI jobs are not executions; only owner-published receipt observations can support a result.",
            "No stable owner export is available for benchmark measurements or coverage reports; these are unavailable, not zero. GitHub Actions run facts are captured separately and do not expose individual job/test results or runner environments here.",
            "Dirty captured trees require complete-scope content comparison with eligible execution endpoints; a commit match alone does not decide applicability or acceptance.",
        ]
    )
    if owner_descriptor.get("coverage_caveat"):
        gaps.append(owner_descriptor["coverage_caveat"])
    return {
        "owner": evidence.get("owner", "agentctl"),
        "interface": evidence.get("interface", "agentctl.evidence.list"),
        "coverage": evidence.get("coverage", "unavailable"),
        "observed_at": evidence.get("observed_at") or observed_at,
        "source_revision": evidence.get("revision"),
        "package_revision": revision,
        "package_project": package_project,
        "owner_project": owner_project,
        "owner_descriptor": owner_descriptor,
        "records": records,
        "gaps": gaps,
        "job_observations": jobs.get("records", []),
        "coverage_document": {
            "captured_package_revision": revision,
            "captured_package_dirty": dirty,
            "package_project": package_project,
            "owner_project": owner_project,
            "owner_descriptor": owner_descriptor,
            "native_evidence": {
                "owner": evidence.get("owner", "agentctl"),
                "interface": evidence.get("interface", "agentctl.evidence.list"),
                "coverage": evidence.get("coverage", "unavailable"),
                "observed_at": evidence.get("observed_at") or observed_at,
                "source_revision": evidence.get("revision"),
                "record_count": len(records),
            },
            "job_observations": {
                "owner": "agentctl",
                "interface": "agentctl.job.list",
                "coverage": jobs.get("coverage", "unavailable"),
                "observed_at": jobs.get("observed_at"),
                "record_count": len(jobs.get("records", [])),
                "caveats": jobs.get("gaps", []),
            },
            "job_execution_details": jobs.get("detail_coverage", {"coverage": "unavailable"}),
            "benchmark_exports": "unavailable",
            "coverage_report_exports": "unavailable",
            "hosted_ci_run_exports": "not_queried_yet",
            "gaps": gaps,
        },
    }


def _owner_project(
    captured_source: Path, package_project: str
) -> tuple[str, dict[str, Any]]:
    """Resolve AgentCTL identity from the captured descriptor with provenance."""
    descriptor = captured_source / ".agentctl/project.toml"
    if not descriptor.is_file():
        return package_project, {
            "path": ".agentctl/project.toml",
            "status": "unavailable",
            "fallback": "package_project_name",
            "coverage_caveat": "Captured AgentCTL project descriptor is absent; owner routes use the package name.",
        }
    raw = descriptor.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return package_project, {
            "path": ".agentctl/project.toml",
            "sha256": digest,
            "status": "invalid",
            "fallback": "package_project_name",
            "coverage_caveat": f"Captured AgentCTL descriptor is invalid ({type(error).__name__}); owner routes use the package name.",
        }
    project = parsed.get("project")
    owner_id = project.get("id") if isinstance(project, dict) else None
    if not isinstance(owner_id, str) or not owner_id.strip():
        return package_project, {
            "path": ".agentctl/project.toml",
            "sha256": digest,
            "status": "invalid",
            "fallback": "package_project_name",
            "coverage_caveat": "Captured AgentCTL descriptor has no [project].id; owner routes use the package name.",
        }
    return owner_id, {
        "path": ".agentctl/project.toml",
        "sha256": digest,
        "status": "resolved",
        "owner_project_id": owner_id,
        "provenance": "captured source file",
    }


def read_ci_runs(
    repo: Path,
    *,
    github_slug: str | None,
    revision: str,
    dirty: bool | None,
    gh_path: str | None = None,
    runner: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read recent GitHub Actions run facts through the public GitHub owner.

    The bounded window is 90 days / 1,000 runs. Run conclusions describe the
    workflow run only; they do not establish that every test or job succeeded.
    ``runner`` is an injectable subprocess seam for offline tests.
    """
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    coverage: dict[str, Any] = {
        "owner": "github-actions",
        "interface": "gh api repos/{slug}/actions/runs",
        "repository": github_slug,
        "window_start": (observed_at - timedelta(days=90)).date().isoformat(),
        "window_end": observed_at.date().isoformat(),
        "observed_at": observed_at.isoformat(),
        "page_size": 100,
        "maximum_records": 1000,
        "coverage": "unavailable",
        "record_count": 0,
        "pages_read": 0,
        "page_observations": [],
        "capped": False,
        "gaps": [],
    }
    if not github_slug:
        coverage["gaps"].append(
            "No configured GitHub repository slug; Actions runs were not queried."
        )
        return {"coverage": coverage, "records": []}
    executable = gh_path or shutil.which("gh")
    if not executable:
        coverage["gaps"].append(
            "gh is unavailable; GitHub Actions run evidence was not queried."
        )
        return {"coverage": coverage, "records": []}

    invoke = runner or subprocess.run
    rows: list[dict[str, Any]] = []
    cutoff = coverage["window_start"]
    for page in range(1, 11):
        command = [
            executable,
            "api",
            f"repos/{github_slug}/actions/runs?per_page=100&page={page}&created=>={cutoff}",
            "--jq",
            ".workflow_runs",
        ]
        try:
            response = invoke(
                command,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            coverage["gaps"].append(
                f"GitHub Actions page {page} failed: {type(error).__name__}: {error}"
            )
            break
        if response.returncode != 0:
            coverage["gaps"].append(
                f"GitHub Actions page {page} failed: gh exited {response.returncode}: {(response.stderr or '')[:200]}"
            )
            break
        try:
            batch = json.loads(response.stdout or "[]")
        except json.JSONDecodeError as error:
            coverage["gaps"].append(
                f"GitHub Actions page {page} returned invalid JSON: {error}"
            )
            break
        if not isinstance(batch, list) or any(
            not isinstance(row, dict) for row in batch
        ):
            coverage["gaps"].append(
                f"GitHub Actions page {page} returned an invalid workflow_runs shape."
            )
            break
        coverage["pages_read"] = page
        page_observed_at = datetime.now(timezone.utc).isoformat()
        coverage["page_observations"].append(
            {"page": page, "observed_at": page_observed_at, "record_count": len(batch)}
        )
        for raw in batch:
            if len(rows) >= 1000:
                break
            head_sha = raw.get("head_sha")
            records_fields = {
                "id": raw.get("id"),
                "name": raw.get("name"),
                "workflow_id": raw.get("workflow_id"),
                "path": raw.get("path"),
                "run_number": raw.get("run_number"),
                "head_sha": head_sha,
                "status": raw.get("status"),
                "conclusion": raw.get("conclusion"),
                "event": raw.get("event"),
                "created_at": raw.get("created_at"),
                "run_started_at": raw.get("run_started_at"),
                "updated_at": raw.get("updated_at"),
                "html_url": raw.get("html_url"),
                "head_branch": raw.get("head_branch"),
                "repository": github_slug,
                "page": page,
                "observed_at": observed_at.isoformat(),
                "page_observed_at": page_observed_at,
                "applicable_to_package": dirty is False and head_sha == revision,
                "command": None,
                "environment": None,
                "interpretation": "Workflow run outcome only; individual test/job outcomes are not inferred.",
                "owner_record": raw,
            }
            rows.append(records_fields)
        if len(rows) >= 1000:
            coverage["capped"] = len(batch) == 100 or page == 10
            break
        if len(batch) < 100:
            break
    coverage["record_count"] = len(rows)
    if coverage["pages_read"] == 0:
        coverage["coverage"] = "unavailable"
    elif coverage["capped"]:
        coverage["coverage"] = "partial_capped"
        coverage["gaps"].append(
            "Results are capped at the most recent 1,000 run records in the 90-day window."
        )
    elif coverage["gaps"]:
        coverage["coverage"] = "partial"
    else:
        coverage["coverage"] = "complete_window"
    return {"coverage": coverage, "records": rows}


def _write_ci_markdown(path: Path, payload: dict[str, Any]) -> None:
    coverage = payload["coverage"]
    lines = [
        "# GitHub Actions run inventory",
        "",
        f"- Repository: `{coverage.get('repository') or 'unknown'}`",
        f"- Coverage: {coverage['coverage']} ({coverage['record_count']} records)",
        f"- Window: {coverage['window_start']} through {coverage['window_end']}",
        f"- Pages read: {coverage['pages_read']}; capped: {coverage['capped']}",
        "",
        "Run conclusions describe workflow runs. They do not prove every test or job passed.",
        "",
        "| Run | Workflow | Event | Revision | Status | Conclusion | Updated | Applies to package |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["records"]:
        lines.append(
            "| {id} | {name} | {event} | `{sha}` | {status} | {conclusion} | {updated} | {applies} |".format(
                id=row.get("id") or "unknown",
                name=(row.get("name") or "unknown").replace("|", "\\|"),
                event=row.get("event") or "unknown",
                sha=row.get("head_sha") or "unknown",
                status=row.get("status") or "unknown",
                conclusion=row.get("conclusion") or "unknown",
                updated=row.get("updated_at") or "unknown",
                applies=row.get("applicable_to_package"),
            )
        )
    if not payload["records"]:
        lines.append("| | No records available | | | | | | |")
    lines.extend(["", "## Coverage gaps", ""])
    lines.extend(
        f"- {gap}"
        for gap in coverage["gaps"] or ["None reported for the queried window."]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reset_context_cache() -> None:
    """Drop process-local AgentCTL observations before a new build."""
    _read_agentctl_snapshot.cache_clear()


@lru_cache(maxsize=1)
def _read_agentctl_snapshot() -> Any:
    """Read the bounded public job list once per Chisel process."""
    return agentctl.read_observation_snapshot()


def _agentctl_jobs(
    project: str, *, selected_revisions: set[str] | None = None
) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    from .chisel_options import active_options
    window_days = active_options.context_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        snapshot = _read_agentctl_snapshot()
    except (agentctl.AgentctlObservationError, OSError) as error:
        return {
            "coverage": "unavailable",
            "observed_at": observed_at,
            "records": [],
            "gaps": [str(error)],
        }
    records = []
    candidates = []
    for row in snapshot.observations:
        if row.project != project:
            continue
        records.append(
            {
                "source_id": row.source_id,
                "project": row.project,
                "operation": row.operation,
                "command": list(row.command),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                "duration_s": row.duration_s,
                "status": row.status,
                "exit_code": row.exit_code,
                "outcome_known": row.outcome_known,
                "git_commit": row.git_commit,
                "git_dirty": row.git_dirty,
                "caveats": json.loads(row.caveats_json),
            }
        )
        reference = getattr(snapshot, "detail_references", {}).get(row.source_id)
        if (row.outcome_known is True and reference and row.started_at and row.started_at >= cutoff
                and any(word in (row.operation or "").lower()
                        for word in ("test", "verify", "check", "benchmark", "qualif"))):
            candidates.append((row, reference))
    # Detail reads are intentionally bounded; the lifecycle list remains complete.
    revisions = selected_revisions or set()
    selected = sorted(
        candidates,
        key=lambda pair: (
            pair[0].git_commit in revisions if pair[0].git_commit else False,
            pair[0].started_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )[:24]
    details = []
    detail_errors = 0
    for row, reference in selected:
        detail = _agentctl_job_detail(row, reference)
        if detail is None:
            detail_errors += 1
        else:
            details.append(detail)
    return {
        "coverage": "all_retained_jobs",
        "observed_at": observed_at,
        "records": records,
        "details": details,
        "detail_coverage": {"selection": "Matching captured revision hints first, then most recent retained terminal test/verify/check/benchmark/qualification jobs with launch references in context window; maximum 24",
                            "window_days": window_days, "window_start": cutoff.isoformat(),
                            "eligible_count": len(candidates), "selected_count": len(selected),
                            "captured_count": len(details), "failed_count": detail_errors,
                            "eligible_revision_hints": sum(row.git_commit in revisions for row, _ in candidates if row.git_commit),
                            "selected_revision_hints": sum(row.git_commit in revisions for row, _ in selected if row.git_commit),
                            "revision_hint_availability": "available" if any(row.git_commit for row, _ in candidates) else "unavailable_in_job_list",
                            "capped": len(candidates) > len(selected)},
        "gaps": list(snapshot.caveats)
        + [
            "AgentCTL's job list is a retained lifecycle view; an operation name or terminal status does not establish that tests, benchmarks, coverage, or CI passed."
        ],
    }


def _agentctl_job_detail(row: Any, reference: str) -> dict[str, Any] | None:
    job_id = row.source_id.removeprefix("agentctl:")
    try:
        result = subprocess.run(
            ["agentctl", "job", "get", job_id, "--reference", reference, "--json"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        detail = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not isinstance(detail, dict) or str(detail.get("job_id")) != job_id or detail.get("reference") != reference:
        return None
    outcome = detail.get("outcome") if isinstance(detail.get("outcome"), dict) else {}
    receipt = outcome.get("execution_receipt") if isinstance(outcome.get("execution_receipt"), dict) else None
    execution = outcome.get("execution_evidence") if isinstance(outcome.get("execution_evidence"), dict) else None
    return {
        "source_id": row.source_id, "reference": reference,
        "project": row.project, "operation": row.operation,
        "phase": detail.get("phase"), "result": detail.get("result"),
        "exit_code": detail.get("exit_code"),
        "started_at": detail.get("started_at"), "ended_at": detail.get("ended_at"),
        "execution_receipt": receipt, "execution_evidence": execution,
        "artifact_refs": detail.get("artifacts"),
        "interpretation": "Job execution and endpoint receipts do not by themselves establish test acceptance or immutable execution.",
    }


def _compact_job_details(jobs: dict[str, Any], package_dir: Path, primary_revision: str) -> dict[str, Any]:
    """Keep owner details once, with reusable content manifests by digest."""
    catalogue = package_dir / "snapshots.json"
    revisions = {primary_revision}
    if catalogue.is_file():
        revisions.update(row["revision"] for row in json.loads(catalogue.read_text()).get("snapshots", [])
                         if isinstance(row, dict) and isinstance(row.get("revision"), str))
    manifest_dir = package_dir / "owners/content-manifests"
    compact = []
    full = summarized = 0
    for detail in jobs.get("details", []):
        entry = dict(detail)
        receipt = entry.get("execution_receipt")
        if isinstance(receipt, dict):
            receipt = dict(receipt)
            start = receipt.get("start") or {}
            end = receipt.get("end") or {}
            relevant = (isinstance(start, dict) and isinstance(end, dict)
                        and start.get("head") == end.get("head")
                        and start.get("head") in revisions)
            for endpoint in ("start", "end"):
                observed = receipt.get(endpoint)
                if not isinstance(observed, dict):
                    continue
                observed = dict(observed)
                manifest = observed.pop("content_manifest", None)
                if isinstance(manifest, dict):
                    observed["content_manifest_summary"] = {
                        key: manifest.get(key) for key in ("schema_version", "sha256", "coverage", "scope", "coherence", "bytes_hashed", "omissions")
                    }
                    if relevant:
                        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                        digest = hashlib.sha256(encoded).hexdigest()
                        manifest_dir.mkdir(parents=True, exist_ok=True)
                        path = manifest_dir / f"{digest}.json"
                        if not path.exists():
                            path.write_bytes(encoded + b"\n")
                        observed["content_manifest_ref"] = f"sha256:{digest}"
                receipt[endpoint] = observed
            entry["execution_receipt"] = receipt
            full += bool(relevant)
            summarized += not relevant
        compact.append(entry)
    coverage = dict(jobs.get("detail_coverage") or {})
    coverage.update({"full_content_details": full, "endpoint_summary_only": summarized,
                     "unique_content_manifests": len(list(manifest_dir.glob("*.json"))) if manifest_dir.exists() else 0})
    return {**jobs, "details": compact, "detail_coverage": coverage}


def _write_verification_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Owner-published verification evidence",
        "",
        f"- Owner route: `{payload['owner']}.{payload['interface']}`",
        f"- Coverage: {payload['coverage']}",
        f"- Observed: {payload['observed_at']}",
        f"- Package revision: `{payload['package_revision']}`",
        f"- Package name / owner project ID: `{payload['package_project']}` / `{payload['owner_project']}`",
        f"- Owner ID provenance: `{payload['owner_descriptor'].get('path')}` ({payload['owner_descriptor'].get('status')}; SHA-256 `{payload['owner_descriptor'].get('sha256', 'unavailable')}`)",
        "",
    ]
    if not payload["records"]:
        lines.append(
            "No retained verification records were returned by the owner route. This means unavailable or no retained records, not that tests passed or failed."
        )
    for record in payload["records"]:
        lines.extend(
            [
                f"## Evidence {record.get('evidence_id') or 'unknown'}",
                "",
                f"- Candidate revision: `{record.get('candidate_revision') or 'unknown'}`",
                f"- Candidate dirty: {record.get('candidate_dirty') if record.get('candidate_dirty') is not None else 'unknown'}",
                "",
            ]
        )
        for check in record["verification"]:
            lines.extend(
                [
                    f"### {check.get('command') or 'Command unknown'}",
                    "",
                    f"- Selection: {check.get('selection') or 'unknown'}",
                    f"- Timestamp: {check.get('timestamp') or 'unknown'}",
                    f"- Environment: {json.dumps(check.get('environment'), ensure_ascii=False) if check.get('environment') is not None else 'unknown'}",
                    f"- Claimed outcome: {check.get('claimed_outcome') or 'unknown'}",
                    f"- Owner observation: {json.dumps(check.get('owner_observation'), ensure_ascii=False)}",
                    f"- Tested revision: `{check.get('tested_revision') or 'unknown'}`",
                    f"- Applies to this package: {check['applicable_to_package']}",
                    "",
                ]
            )
    lines.extend(["## AgentCTL job observations", ""])
    if not payload["job_observations"]:
        lines.append("No retained AgentCTL job rows were returned for this project.")
    for job in payload["job_observations"]:
        lines.extend(
            [
                f"### {job.get('source_id') or 'Job'}: {job.get('operation') or 'operation unknown'}",
                "",
                f"- Status: {job.get('status') or 'unknown'} (lifecycle only; not a test result)",
                f"- Exit code: {job.get('exit_code') if job.get('exit_code') is not None else 'unknown'}",
                f"- Started: {job.get('started_at') or 'unknown'}",
                f"- Ended: {job.get('ended_at') or 'unknown'}",
                f"- Revision: `{job.get('git_commit') or 'unknown'}`",
                "",
            ]
        )
    lines.extend(["## Gaps", "", *[f"- {gap}" for gap in payload["gaps"]]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
