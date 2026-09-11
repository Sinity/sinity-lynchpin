"""Bounded temporal products over structured retained AgentCTL observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any

from lynchpin.analysis.projects.campaign import (
    _attempts,
    _object,
    _published,
    _rows,
    _source,
    _timestamp,
    _verification_proof,
)
from lynchpin.sources.campaign import read_batches
from lynchpin.core.primitives import logical_date


def _selected_attempts(
    project: str,
    snapshot: dict[str, Any],
    bead_refs: list[str] | None,
) -> list[dict[str, Any]]:
    runs = [row for row in _rows(snapshot.get("rows")) if row.get("project") == project]
    prefix = f"sinnix://projects/{project}/beads/"
    if bead_refs is not None and any(not ref.startswith(prefix) for ref in bead_refs):
        raise ValueError("bead_refs must belong to the selected project")
    refs = set(bead_refs or [])
    if bead_refs is None:
        for run in runs:
            for worker in _rows(run.get("workers")):
                ids = list(worker.get("beads") or []) + [
                    row.get("id")
                    for row in _rows(_object(worker.get("result")).get("beads"))
                ]
                refs.update(
                    value if value.startswith(prefix) else prefix + value
                    for value in ids
                    if isinstance(value, str) and "/" not in value.removeprefix(prefix)
                )
    return [
        dict(attempt, bead_ref=ref)
        for ref in sorted(refs)
        for attempt in _attempts(ref, runs)
    ]


def _base(
    product: str, project: str, snapshot: dict[str, Any], refresh_id: str | None
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product": product,
        "project": project,
        "outcome": "partial",
        "sources": {"agentctl": _source(snapshot, "agentctl")},
        "coverage": "retained_records",
        "complete_history": False,
        "temporal": {
            "requested_refresh_id": refresh_id,
            "refresh_id": None,
            "observed_at": snapshot.get("observed_at"),
            "watermark": snapshot.get("watermark"),
        },
        "gaps": list(snapshot.get("gaps", []))
        + ["Retained owner records do not establish complete project history"],
    }


def verification_regression(
    *,
    project: str,
    bead_refs: list[str] | None = None,
    refresh_id: str | None = None,
    runtime_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare recorded outcomes of the same command, AC scope, and Beads revision."""
    snapshot = (
        runtime_snapshot if runtime_snapshot is not None else read_batches(project)
    )
    result = _base("verification_regression", project, snapshot, refresh_id)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded = []
    for attempt in _selected_attempts(project, snapshot, bead_refs):
        for check in attempt["verification"]:
            coverage = _object(check.get("coverage"))
            ac_ids = check.get("ac_ids", coverage.get("ac_ids"))
            key = {
                "bead_ref": attempt["bead_ref"],
                "bead_revision": attempt["bead_revision"],
                "command": check.get("command"),
                "scope": coverage.get("scope"),
                "ac_ids": sorted(set(ac_ids))
                if isinstance(ac_ids, list)
                and all(isinstance(ac, str) for ac in ac_ids)
                else [],
            }
            if not all(
                (
                    key["bead_revision"],
                    key["command"],
                    key["scope"],
                    key["ac_ids"],
                    check.get("receipt"),
                    check.get("tested_sha"),
                )
            ) or check.get("status") not in {"passed", "failed", "skipped"}:
                excluded.append(
                    {
                        "source_ref": attempt["source_ref"],
                        "reason": "Verification lacks comparable structured command, AC scope, revision, tested SHA, outcome, or receipt",
                    }
                )
                continue
            event_time = (
                attempt["result_recorded_at"] or attempt["acceptance_recorded_at"]
            )
            groups[json.dumps(key, sort_keys=True)].append(
                {
                    "status": check["status"],
                    "tested_sha": check["tested_sha"],
                    "receipt": check["receipt"],
                    "source_ref": attempt["source_ref"],
                    "attempt_id": attempt["id"],
                    "outcome_authority": "owner_corroborated"
                    if _verification_proof(check, attempt)["corroborated"]
                    else "worker_claim",
                    "owner_corroboration": _verification_proof(check, attempt),
                    "event_time": event_time,
                    "event_time_basis": "result_or_acceptance_recorded_at"
                    if event_time
                    else None,
                    "attempt_started_at": attempt["event_time"],
                }
            )
    comparisons = []
    for serialized_key, values in sorted(groups.items()):
        observations = sorted(
            values,
            key=lambda row: (
                _timestamp(row["event_time"])
                or datetime.min.replace(tzinfo=timezone.utc),
                row["source_ref"],
                row["status"],
            ),
        )
        transitions = []
        for previous, current in zip(observations, observations[1:]):
            first, second = (
                _timestamp(previous["event_time"]),
                _timestamp(current["event_time"]),
            )
            if first is None or second is None or first >= second:
                change = "order_unknown"
            elif previous["status"] == "passed" and current["status"] == "failed":
                change = (
                    "observed_regression"
                    if all(
                        row["outcome_authority"] == "owner_corroborated"
                        for row in (previous, current)
                    )
                    else "reported_regression"
                )
            elif previous["status"] == "failed" and current["status"] == "passed":
                change = (
                    "observed_recovery"
                    if all(
                        row["outcome_authority"] == "owner_corroborated"
                        for row in (previous, current)
                    )
                    else "reported_recovery"
                )
            elif previous["status"] == current["status"]:
                change = "unchanged"
            else:
                change = "coverage_changed"
            transitions.append({"kind": change, "before": previous, "after": current})
        comparisons.append(
            {
                **json.loads(serialized_key),
                "observations": observations,
                "transitions": transitions,
            }
        )
    result.update(
        groups=comparisons,
        excluded=excluded,
        counts={
            "groups_observed": len(comparisons),
            "observations": sum(len(group["observations"]) for group in comparisons),
            "regressions_observed": sum(
                t["kind"] == "observed_regression"
                for g in comparisons
                for t in g["transitions"]
            ),
            "reported_regressions": sum(
                t["kind"] == "reported_regression"
                for g in comparisons
                for t in g["transitions"]
            ),
            "complete_project_regressions": None,
        },
    )
    if not comparisons:
        result["outcome"] = "unavailable"
        result["gaps"].append("No comparable structured verification observations")
    result["gaps"].append(
        "Worker-reported outcomes remain claims unless an owner receipt corroborates them; outcome changes do not establish causation"
    )
    return result


def project_trajectory(
    *,
    project: str,
    bead_refs: list[str] | None = None,
    refresh_id: str | None = None,
    runtime_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Daily retained attempts and published batches, without inferring full throughput."""
    snapshot = (
        runtime_snapshot if runtime_snapshot is not None else read_batches(project)
    )
    result = _base("project_trajectory", project, snapshot, refresh_id)
    attempts = _selected_attempts(project, snapshot, bead_refs)
    days: dict[str, dict[str, Any]] = {}
    observed_attempts: set[str] = set()
    observed_publications: set[str] = set()
    attempt_time_bases: dict[str, int] = defaultdict(int)
    undated = []
    for attempt in attempts:
        for kind, identity, timestamp in (
            ("attempts_observed", attempt["source_ref"], attempt["event_time"]),
            (
                "published_batches_observed",
                attempt["run_id"],
                attempt["acceptance_recorded_at"],
            ),
        ):
            seen = (
                observed_attempts
                if kind == "attempts_observed"
                else observed_publications
            )
            if (
                identity in seen
                or (kind == "attempts_observed" and not attempt["attempt_observed"])
                or (kind == "published_batches_observed" and not _published(attempt))
            ):
                continue
            seen.add(identity)
            stamp = _timestamp(timestamp)
            time_basis = (
                attempt["event_time_basis"]
                if kind == "attempts_observed"
                else "acceptance_recorded_at"
            )
            if kind == "attempts_observed":
                attempt_time_bases[time_basis if stamp else "unknown"] += 1
            if stamp is None:
                undated.append(
                    {
                        "kind": kind,
                        "source_ref": attempt["source_ref"],
                        "event_time_basis": None,
                    }
                )
                continue
            day = logical_date(stamp).isoformat()
            row = days.setdefault(
                day,
                {
                    "day": day,
                    "attempts_observed": 0,
                    "published_batches_observed": 0,
                    "evidence": [],
                },
            )
            row[kind] += 1
            row["evidence"].append(
                {
                    "kind": kind,
                    "source_ref": attempt["source_ref"],
                    "attempt_id": attempt["id"],
                    "run_id": attempt["run_id"],
                    "event_time": timestamp,
                    "event_time_basis": time_basis,
                    "integrated_sha": attempt["integrated_sha"],
                    "publication": attempt["publication"],
                    "requested_model": attempt["planned_model"],
                    "actual_executor_model": attempt["actual_executor_model"],
                    "measured_usage": attempt["usage"],
                }
            )
    result.update(
        rows=[days[day] for day in sorted(days)],
        undated=undated,
        counts={
            "attempts_observed": len(observed_attempts),
            "published_batches_observed": len(observed_publications),
            "complete_project_throughput": None,
        },
        time_basis={
            "attempts": next(iter(attempt_time_bases))
            if len(attempt_time_bases) == 1
            else "mixed"
            if attempt_time_bases
            else None,
            "attempt_basis_counts": dict(attempt_time_bases),
            "publication": "acceptance_recorded_at",
            "day": "local_logical_date",
        },
    )
    if not observed_attempts and not observed_publications:
        result["outcome"] = "unavailable"
        result["gaps"].append("No retained explicit attempt or publication records")
    result["gaps"].append(
        "A retained worker records its latest result; prior job IDs do not reconstruct deleted or replaced attempts"
    )
    return result
