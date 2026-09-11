"""Revision-bound campaign evidence. Task closure is independent of proof."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from typing import Any

from lynchpin.sources.campaign import (
    read_batches,
    read_native_evidence,
    read_session_evidence,
    revision,
)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return (
        [row for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _source(snapshot: dict[str, Any], owner: str) -> dict[str, Any]:
    temporal = _object(snapshot.get("temporal"))
    return {
        "owner": owner,
        "revision": snapshot.get(
            "revision", snapshot.get("task_revision", temporal.get("resolved_revision"))
        ),
        "revision_kind": snapshot.get("revision_kind", "owner_revision"),
        "revision_domain": snapshot.get(
            "revision_domain", "dolt_snapshot" if owner == "beads" else None
        ),
        "observed_at": snapshot.get("observed_at", temporal.get("observed_at")),
        "watermark": snapshot.get("watermark", temporal.get("watermark")),
        "coverage": snapshot.get("coverage", "unknown"),
        "gaps": snapshot.get("gaps", []),
    }


def _task_rows(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        row["ref"]: row
        for row in _rows(
            snapshot.get("nodes", snapshot.get("items", snapshot.get("beads")))
        )
        if isinstance(row.get("ref"), str)
    }


def _fields(task: dict[str, Any]) -> dict[str, Any]:
    return _object(task.get("fields")) or task


def _metadata(fields: dict[str, Any]) -> dict[str, Any]:
    value = fields.get("metadata")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return _object(value)


def _owner_revision(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _bead_revision(task: dict[str, Any]) -> str | None:
    if (
        task.get("bead_revision_domain") != "beads_row_revision"
        or task.get("bead_revision_coverage") == "unavailable"
    ):
        return None
    return _owner_revision(task.get("bead_revision"))


def _matches(value: Any, bead_ref: str) -> bool:
    return isinstance(value, str) and value in {bead_ref, bead_ref.rsplit("/", 1)[-1]}


def _worker_attempts(worker: dict[str, Any]) -> list[dict[str, Any]]:
    launches = _rows(worker.get("attempts"))
    if not launches:
        return [
            dict(worker, launch={}, legacy_attempt_history=True, current_attempt=True)
        ]
    rows = []
    for index, launch in enumerate(launches):
        current = index == len(launches) - 1
        row = dict(
            worker, launch=launch, legacy_attempt_history=False, current_attempt=current
        )
        row.update({key: launch.get(key) for key in ("task_id", "task_reference")})
        if not current:
            row.update(
                result=launch.get("result"),
                provenance=launch.get("provenance"),
                result_recorded_at=launch.get("result_recorded_at"),
                stage=None,
            )
        rows.append(row)
    return rows


def _attempts(bead_ref: str, batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts = []
    for run in batches:
        acceptance = _object(run.get("acceptance"))
        landing = _object(run.get("landing"))
        publication = _object(acceptance.get("published"))
        for worker in [
            row
            for native in _rows(run.get("workers"))
            for row in _worker_attempts(native)
        ]:
            result = _object(worker.get("result"))
            launch = _object(worker.get("launch"))
            provenance = _object(worker.get("provenance"))
            observed = _object(provenance.get("observed_executor"))
            worker_claim = provenance.get("worker_claim") or {
                key: result[key]
                for key in (
                    "planned_model",
                    "actual_executor_model",
                    "actual_executor_observed_by",
                    "measured_usage",
                    "model_segments",
                    "parent_session_ref",
                    "child_session_ref",
                )
                if key in result
            }
            entries = [
                entry
                for entry in _rows(result.get("beads"))
                if _matches(entry.get("id"), bead_ref)
            ]
            if not entries and not any(
                _matches(value, bead_ref) for value in worker.get("beads", [])
            ):
                continue
            integrated_sha = acceptance.get(
                "candidate_sha", landing.get("candidate_sha")
            )
            attempts.append(
                {
                    "id": worker.get("task_reference"),
                    "run_id": run.get("run_id"),
                    "worker_id": worker.get("id"),
                    "job_id": worker.get("task_id"),
                    "prior_job_ids": worker.get("task_ids", []),
                    "attempt": launch.get("number"),
                    "dispatch_bead_revision": _owner_revision(
                        _object(worker.get("bead_revisions")).get(
                            bead_ref.rsplit("/", 1)[-1]
                        )
                    ),
                    "bead_revision": next(
                        (
                            _owner_revision(entry.get("bead_revision"))
                            for entry in entries
                        ),
                        None,
                    ),
                    "event_time": launch.get(
                        "recorded_at",
                        run.get("created_at")
                        if launch.get("number") in {None, 1}
                        else None,
                    ),
                    "event_time_basis": "attempt_recorded_at"
                    if launch.get("recorded_at")
                    else "batch_created_at_lower_bound",
                    "result_recorded_at": worker.get("result_recorded_at"),
                    "runtime_revision": run.get("runtime_revision"),
                    "stage": worker.get("stage"),
                    "attempt_observed": bool(
                        result
                        or worker.get("task_reference")
                        or worker.get("task_id") is not None
                    ),
                    "criteria": [c for e in entries for c in _rows(e.get("criteria"))],
                    "worker_sha": result.get("candidate_sha"),
                    "integrated_sha": integrated_sha,
                    "verification": _rows(result.get("verification")),
                    "candidate_verification": _object(acceptance.get("verify_run")),
                    "publication": publication,
                    "pull_request": _object(landing.get("pr")),
                    "acceptance_recorded_at": acceptance.get("recorded_at"),
                    "unresolved": result.get("unresolved"),
                    "usage": observed.get("measured_usage"),
                    "execution": run.get("harness"),
                    "planned_model": launch.get("model"),
                    "actual_executor_model": observed.get("actual_executor_model"),
                    "actual_executor_observed_by": observed.get("source"),
                    "model_segments": observed.get("model_segments"),
                    "worker_claim": worker_claim or None,
                    "provenance": provenance,
                    "result_available": bool(result),
                    "legacy_attempt_history": worker["legacy_attempt_history"],
                    "current_attempt": worker["current_attempt"],
                    "parent_session_ref": result.get("parent_session_ref"),
                    "child_session_ref": result.get("child_session_ref"),
                    "source_ref": f"agentctl://runs/{run.get('run_id')}/workers/{worker.get('id')}"
                    + (
                        f"/attempts/{launch['number']}"
                        if launch.get("number") is not None
                        else ""
                    ),
                }
            )
    return attempts


def _native_binding(
    record: dict[str, Any], bead_ref: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the owner-published task binding for this explicit Beads ref."""
    for task in _rows(record.get("task_snapshot")):
        binding = _object(task.get("evidence_binding"))
        if _matches(task.get("id", task.get("bead_ref")), bead_ref):
            return task, binding
    return None


def _native_attempts(
    bead_ref: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join native evidence records as attempts without inventing batch ancestry."""
    attempts = []
    for record in records:
        matched = _native_binding(record, bead_ref)
        if matched is None:
            continue
        task, binding = matched
        result = _object(record.get("worker_result"))
        candidate = _object(record.get("candidate"))
        publication = _object(record.get("publication"))
        claims = [
            entry
            for entry in _rows(result.get("beads"))
            if _matches(entry.get("id"), bead_ref)
        ]
        verification = []
        for row in _rows(record.get("verification")):
            claim = _object(row.get("claim"))
            observation = _object(row.get("observation"))
            if claim:
                verification.append({**claim, "owner_observation": observation})
        binding_revision = _owner_revision(binding.get("bead_revision"))
        attempts.append(
            {
                "id": record.get("evidence_id"),
                "run_id": None,
                "worker_id": None,
                "job_id": None,
                "prior_job_ids": [],
                "attempt": None,
                "dispatch_bead_revision": binding_revision,
                "bead_revision": next(
                    (_owner_revision(entry.get("bead_revision")) for entry in claims),
                    None,
                ),
                "binding_bead_revision": binding_revision,
                "binding_criteria": _rows(binding.get("criteria")),
                "binding_v2_available": binding.get("v2_available") is True,
                "binding_reason": binding.get("reason"),
                "event_time": record.get("recorded_at"),
                "event_time_basis": "native_evidence_recorded_at",
                "result_recorded_at": record.get("recorded_at"),
                "runtime_revision": record.get("evidence_id"),
                "stage": None,
                "attempt_observed": True,
                "criteria": [
                    criterion
                    for entry in claims
                    for criterion in _rows(entry.get("criteria"))
                ],
                "worker_sha": result.get("candidate_sha"),
                "integrated_sha": candidate.get("candidate_sha")
                if candidate.get("checked") is True
                else None,
                "candidate": candidate,
                "verification": verification,
                "candidate_verification": {},
                "publication": publication,
                "pull_request": {},
                "acceptance_recorded_at": record.get("recorded_at"),
                "unresolved": result.get("unresolved"),
                "usage": None,
                "execution": "native_evidence",
                "planned_model": None,
                "actual_executor_model": None,
                "actual_executor_observed_by": None,
                "model_segments": None,
                "worker_claim": result or None,
                "provenance": {"worker_claim": result} if result else {},
                "result_available": bool(result),
                "legacy_attempt_history": False,
                "current_attempt": True,
                "parent_session_ref": _object(record.get("session_claims")).get(
                    "parent_session_ref"
                ),
                "child_session_ref": _object(record.get("session_claims")).get(
                    "child_session_ref"
                ),
                "source_kind": "native_evidence",
                "source_ref": f"agentctl://evidence/{record.get('evidence_id')}",
                "native_task_snapshot": task,
            }
        )
    return attempts


def _publication_state(attempt: dict[str, Any]) -> bool | None:
    """True/false only when the owner has observed publication or its contrary."""
    if (
        attempt.get("temporally_eligible") is False
        or attempt.get("publication_temporally_eligible") is False
        or attempt.get("current_attempt") is False
    ):
        return None
    publication = attempt["publication"]
    if attempt.get("source_kind") == "native_evidence":
        state = publication.get("state")
        if (
            state == "published"
            and publication.get("checked") is True
            and publication.get("candidate_reachable") is True
            and attempt.get("candidate", {}).get("checked") is True
        ):
            return True
        if (
            state in {"unpublished", "not_published", "rejected"}
            and publication.get("checked") is True
        ):
            return False
        return None
    integrated = attempt["integrated_sha"]
    if not integrated or publication.get("candidate_sha") != integrated:
        return None
    if publication.get("policy") == "master":
        return True if attempt["acceptance_recorded_at"] else None
    pull = attempt["pull_request"]
    if pull.get("state") in {"OPEN", "CLOSED", "open", "closed"}:
        return False
    merged = _object(pull.get("mergeCommit")).get("oid")
    if merged and merged != publication.get("merge_commit"):
        return False
    if publication.get("merge_commit") and attempt["acceptance_recorded_at"]:
        return True
    return None


def _published(attempt: dict[str, Any]) -> bool:
    return _publication_state(attempt) is True


def _verification_proof(
    check: dict[str, Any], attempt: dict[str, Any]
) -> dict[str, Any]:
    if attempt.get("source_kind") == "native_evidence":
        observation = _object(check.get("owner_observation"))
        execution_receipt = _object(observation.get("execution_receipt"))
        endpoints = [
            _object(execution_receipt.get("start")),
            _object(execution_receipt.get("end")),
        ]
        candidate = _object(attempt.get("candidate"))
        candidate_sha = attempt.get("integrated_sha")
        refs = [observation.get("reference")]
        if observation.get("job_id") is not None:
            refs.append(f"agentctl://jobs/{observation['job_id']}")
            if isinstance(observation.get("reference"), str):
                refs.append(
                    f"agentctl://jobs/{observation['job_id']}/{observation['reference']}"
                )
        linked = bool(check.get("receipt") and check["receipt"] in refs)
        same_sha = bool(
            candidate_sha
            and candidate.get("checked") is True
            and check.get("tested_sha") == candidate_sha
            and all(
                endpoint.get("status") == "observed"
                and endpoint.get("head") == candidate_sha
                for endpoint in endpoints
            )
        )
        clean = all(endpoint.get("dirty") is False for endpoint in endpoints)
        endpoint_bound = execution_receipt.get("binding") == "unchanged_endpoints"
        passed = observation.get("phase") in {
            "succeeded",
            "passed",
        } and observation.get("exit_code") in {None, 0}
        failed = (
            observation.get("phase") == "failed"
            and isinstance(observation.get("exit_code"), int)
            and observation["exit_code"] != 0
        )
        status_matches = (
            passed
            if check.get("status") == "passed"
            else failed
            if check.get("status") == "failed"
            else observation.get("phase") == check.get("status")
        )
        outcome_eligible = (
            observation.get("eligible") is True
            if check.get("status") == "passed"
            else failed
            if check.get("status") == "failed"
            else False
        )
        return {
            "corroborated": linked
            and same_sha
            and clean
            and status_matches
            and observation.get("checked") is True
            and outcome_eligible
            and endpoint_bound,
            "receipt_linked": linked,
            "tested_sha_matches": same_sha,
            "clean_checkout": clean,
            "requested_sha_matches": True,
            "owner": "agentctl",
            "observation": observation,
        }
    owner = attempt["candidate_verification"]
    owner_sha = owner.get("tested_sha", owner.get("candidate_sha"))
    refs = [owner.get("reference"), owner.get("receipt"), owner.get("result_path")]
    if owner.get("job_id") is not None:
        refs.append(f"agentctl://jobs/{owner['job_id']}")
    linked = bool(check.get("receipt") and check["receipt"] in refs)
    same_sha = bool(
        owner_sha and owner_sha == check.get("tested_sha") == attempt["integrated_sha"]
    )
    clean = (
        owner.get("git_dirty") is False
        and check.get("git_dirty") is not True
        and check.get("dirty") is not True
    )
    requested_matches = all(
        value in {None, owner_sha}
        for value in (owner.get("requested_sha"), check.get("requested_sha"))
    )
    passed = owner.get("phase", owner.get("status")) in {"succeeded", "passed"}
    status_matches = (
        passed
        if check.get("status") == "passed"
        else owner.get("phase", owner.get("status")) == check.get("status")
    )
    return {
        "corroborated": linked
        and same_sha
        and clean
        and requested_matches
        and status_matches,
        "receipt_linked": linked,
        "tested_sha_matches": same_sha,
        "clean_checkout": clean,
        "requested_sha_matches": requested_matches,
        "owner": "agentctl",
        "observation": owner,
    }


def _criteria(task: dict[str, Any]) -> list[dict[str, Any]]:
    fields = _fields(task)
    native = fields.get("acceptance_criteria")
    if isinstance(native, list):
        return _rows(native)
    return _rows(_metadata(fields).get("acceptance_criteria"))


def _criterion(
    criterion: dict[str, Any],
    bead_revision: str | None,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    ac_id = criterion.get("id", criterion.get("ac_id"))
    ac_revision = bead_revision
    evidence = []
    gaps = []
    outcomes: set[str] = set()
    for attempt in attempts:
        for claim in attempt["criteria"]:
            if not ac_id or claim.get("id", claim.get("ac_id")) != ac_id:
                continue
            claim_revision = attempt["bead_revision"]
            same_revision = bool(
                ac_revision
                and claim_revision == ac_revision
                and attempt["bead_revision"] == ac_revision
                and attempt["dispatch_bead_revision"] == ac_revision
            )
            selected_text = criterion.get("text")
            claimed_text = claim.get("text")
            same_content = (
                isinstance(selected_text, str)
                and bool(selected_text)
                and selected_text == claimed_text
            )
            binding_matches = (
                any(
                    binding.get("id", binding.get("ac_id")) == ac_id
                    and binding.get("text") == selected_text
                    for binding in attempt.get("binding_criteria", [])
                )
                if attempt.get("source_kind") == "native_evidence"
                else True
            )
            binding_qualified = (
                attempt.get("binding_v2_available") is True and binding_matches
                if attempt.get("source_kind") == "native_evidence"
                else True
            )
            verification = [
                v
                for v in attempt["verification"]
                if ac_id
                in v.get("ac_ids", _object(v.get("coverage")).get("ac_ids", []))
            ]
            integrated_sha = attempt["integrated_sha"]
            proven = [
                v
                for v in verification
                if integrated_sha
                and v.get("tested_sha") == integrated_sha
                and v.get("status", v.get("phase")) in {"passed", "succeeded"}
                and v.get("receipt")
                and _verification_proof(v, attempt)["corroborated"]
            ]
            eligible = attempt.get("temporally_eligible") is not False
            qualified = (
                eligible
                and same_revision
                and same_content
                and binding_qualified
                and bool(proven)
                and _published(attempt)
            )
            outcome = claim.get("status")
            if (
                eligible
                and same_revision
                and same_content
                and outcome in {"satisfied", "unsatisfied"}
            ):
                outcomes.add(outcome)
                if any(
                    integrated_sha
                    and check.get("tested_sha") == integrated_sha
                    and check.get("status") == "failed"
                    and check.get("receipt")
                    for check in verification
                ):
                    outcomes.add("unsatisfied")
            evidence.append(
                {
                    "ac_id": ac_id,
                    "source_ref": attempt["source_ref"],
                    "attempt_id": attempt["id"],
                    "bead_revision": attempt["bead_revision"],
                    "ac_revision": claim_revision,
                    "claim": outcome,
                    "same_revision": same_revision,
                    "same_acceptance_content": same_content,
                    "owner_task_binding": binding_qualified,
                    "selected_acceptance_text": selected_text,
                    "claimed_acceptance_text": claimed_text,
                    "verified": qualified,
                    "temporally_eligible": eligible,
                    "worker_sha": attempt["worker_sha"],
                    "integrated_sha": integrated_sha,
                    "merge_commit": attempt["publication"].get("merge_commit"),
                    "publication": attempt["publication"],
                    "pull_request": attempt["pull_request"],
                    "verification": [
                        dict(
                            check,
                            owner_corroboration=_verification_proof(check, attempt),
                        )
                        for check in verification
                    ],
                }
            )
            if not same_revision:
                gaps.append(
                    "Acceptance revision is missing or differs from the selected obligation"
                )
            if not same_content:
                gaps.append(
                    "Acceptance content is missing or differs; row revision equality does not prove unchanged acceptance text"
                )
            if not binding_qualified:
                gaps.append(
                    "Native task binding does not confirm this criterion at the selected Beads revision"
                )
            if not proven:
                gaps.append("No passing AC-linked receipt tests the integrated SHA")
            if not _published(attempt):
                gaps.append("Publication of the integrated candidate is unproved")
            if not eligible:
                gaps.append(
                    "Evidence is later than the requested time or lacks a result timestamp"
                )
    if len(outcomes) > 1:
        state = "contradicted"
    elif outcomes == {"unsatisfied"}:
        state = "unsatisfied"
    elif any(row["verified"] and row["claim"] == "satisfied" for row in evidence):
        state = "verified"
    else:
        state = "unknown"
    if not ac_id:
        gaps.append("Stable acceptance criterion identity is unavailable")
    if not evidence:
        gaps.append("No structured evidence links this acceptance criterion")
    return {
        "id": ac_id,
        "revision": ac_revision,
        "revision_domain": "beads_row_revision" if ac_revision is not None else None,
        "acceptance_revision": criterion.get("revision"),
        "state": state,
        "evidence": evidence,
        "gaps": sorted(set(gaps)),
    }


def campaign_evidence(
    *,
    project: str,
    bead_refs: list[str],
    task_snapshot: dict[str, Any] | None = None,
    runtime_snapshot: dict[str, Any] | None = None,
    native_evidence_snapshot: dict[str, Any] | None = None,
    refresh_id: str | None = None,
    session_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join explicit Beads obligations to owner-published runtime evidence."""
    prefix = f"sinnix://projects/{project}/beads/"
    if (
        not project
        or not bead_refs
        or any(
            not ref.startswith(prefix)
            or not ref[len(prefix) :]
            or "/" in ref[len(prefix) :]
            for ref in bead_refs
        )
    ):
        raise ValueError(
            "bead_refs must be canonical references in the selected project"
        )
    snapshot = task_snapshot or {}
    temporal = _object(snapshot.get("temporal"))
    requested = temporal.get("requested")
    historical = (
        requested not in {None, "current", "HEAD", "now", "latest"}
        if isinstance(requested, (str, type(None)))
        else True
    )
    cutoff = (
        (_timestamp(requested) or _timestamp(temporal.get("effective_at")))
        if historical
        else None
    )
    runtime = (
        runtime_snapshot if runtime_snapshot is not None else read_batches(project)
    )
    native = (
        native_evidence_snapshot
        if native_evidence_snapshot is not None
        else read_native_evidence(project)
        if runtime_snapshot is None
        else {
            "rows": [],
            "coverage": "unavailable",
            "gaps": [],
        }
    )
    sources = {
        "beads": _source(snapshot, "beads"),
        "agentctl": _source(runtime, "agentctl"),
        "agentctl_native": _source(native, "agentctl"),
    }
    tasks = _task_rows(snapshot)
    batches = [r for r in _rows(runtime.get("rows")) if r.get("project") == project]
    native_records = [
        r for r in _rows(native.get("rows")) if r.get("project") == project
    ]
    pulls = [
        _object(row.get("landing")).get("pr")
        for row in batches
        if _object(row.get("landing")).get("pr")
    ]
    sources["github"] = {
        "owner": "github",
        "revision": revision(pulls) if pulls else None,
        "revision_kind": "observation_digest",
        "observed_at": runtime.get("observed_at") if pulls else None,
        "watermark": None,
        "coverage": "referenced_prs" if pulls else "unavailable",
        "gaps": []
        if pulls
        else [
            "No independently observed GitHub PR records; publication receipts remain AgentCTL-owned evidence"
        ],
    }
    items = []
    for ref in dict.fromkeys(bead_refs):
        task = tasks.get(ref, {})
        fields = _fields(task)
        task_revision = task.get(
            "task_revision",
            snapshot.get(
                "task_revision",
                snapshot.get("revision", temporal.get("resolved_revision")),
            ),
        )
        bead_revision = _bead_revision(task)
        attempts = [*_attempts(ref, batches), *_native_attempts(ref, native_records)]
        if historical:
            selected = []
            for attempt in attempts:
                event_time = _timestamp(attempt["event_time"])
                if cutoff and event_time and event_time > cutoff:
                    continue
                result_time = _timestamp(attempt["result_recorded_at"]) or _timestamp(
                    attempt["acceptance_recorded_at"]
                )
                attempt["temporally_eligible"] = bool(
                    cutoff and result_time and result_time <= cutoff
                )
                publication_time = _timestamp(attempt["acceptance_recorded_at"])
                attempt["publication_temporally_eligible"] = bool(
                    cutoff and publication_time and publication_time <= cutoff
                )
                attempt["attempt_observed_by_cutoff"] = bool(
                    cutoff and event_time and event_time <= cutoff
                )
                selected.append(attempt)
            attempts = selected
        acceptance = [_criterion(ac, bead_revision, attempts) for ac in _criteria(task)]
        gaps = []
        ids = [ac["id"] for ac in acceptance if ac["id"]]
        if len(ids) != len(set(ids)):
            for ac in acceptance:
                ac["state"] = "unknown"
                ac["gaps"].append(
                    "Duplicate acceptance criterion IDs make the selected obligation ambiguous"
                )
        if not task:
            gaps.append("Selected Beads record is unavailable")
        if bead_revision is None:
            gaps.append(
                "Selected Beads row revision is unavailable or has an incomparable domain; the Dolt snapshot revision cannot replace it"
            )
        if not acceptance:
            gaps.append(
                "Acceptance criteria are legacy text or missing; completion is unknown"
            )
        if any(not a["id"] for a in attempts):
            gaps.append("Legacy runtime record has no durable attempt identity")
        if any(a["prior_job_ids"] for a in attempts):
            gaps.append(
                "Prior job IDs are retained references, not reconstructed attempt results"
            )
        if any(not a["result_available"] for a in attempts):
            gaps.append(
                "Some recorded launches have no retained result; outcomes and usage remain unknown"
            )
        publication_states = [_publication_state(attempt) for attempt in attempts]
        landed = (
            True
            if True in publication_states
            else False
            if False in publication_states
            and runtime.get("coverage") == "complete"
            and native.get("coverage") == "complete"
            else None
        )
        verification_records = [
            (attempt, check, _verification_proof(check, attempt))
            for attempt in attempts
            if attempt.get("temporally_eligible") is not False
            for check in attempt["verification"]
        ]
        verification_available = any(
            _object(check.get("owner_observation")).get("checked") is True
            if attempt.get("source_kind") == "native_evidence"
            else bool(attempt["candidate_verification"])
            for attempt, check, _proof in verification_records
        )
        verification_state = (
            "verified"
            if any(
                proof["corroborated"]
                for _attempt, check, proof in verification_records
                if check.get("status", check.get("phase")) in {"passed", "succeeded"}
            )
            else "failed"
            if any(
                proof["corroborated"]
                for _attempt, check, proof in verification_records
                if check.get("status", check.get("phase")) == "failed"
            )
            else "unknown"
        )
        publication_available = any(
            attempt.get("publication_temporally_eligible") is not False
            and (
                _object(attempt["publication"]).get("checked") is True
                if attempt.get("source_kind") == "native_evidence"
                else bool(attempt["publication"])
            )
            for attempt in attempts
        )
        disposition = _metadata(fields).get("disposition")
        if disposition in {"superseded", "decomposed"}:
            evidence_state = disposition
        elif acceptance and all(ac["state"] == "verified" for ac in acceptance):
            evidence_state = "verified"
        elif landed is True:
            evidence_state = "implementation_landed_ac_incomplete"
        elif any(
            a["attempt_observed"]
            and (not historical or a["attempt_observed_by_cutoff"])
            for a in attempts
        ):
            evidence_state = "attempted"
        elif (
            runtime.get("coverage") == "complete"
            and native.get("coverage") == "complete"
            and task
        ):
            evidence_state = "unstarted"
        else:
            evidence_state = "unknown"
        items.append(
            {
                "bead_ref": ref,
                "task_revision": task_revision,
                "bead_revision": bead_revision,
                "bead_revision_domain": "beads_row_revision"
                if bead_revision is not None
                else None,
                "bead_revision_coverage": task.get("bead_revision_coverage", "unknown"),
                "bead_revision_unavailable_reason": task.get(
                    "bead_revision_unavailable_reason"
                ),
                "task_status": fields.get("status", task.get("status")),
                "evidence_state": evidence_state,
                "acceptance": acceptance,
                "attempts": attempts,
                "implementation_landed": landed,
                "publication": {
                    "available": publication_available,
                    "state": "published"
                    if landed is True
                    else "not_published"
                    if landed is False
                    else "unknown",
                },
                "verification": {
                    "available": verification_available,
                    "state": verification_state,
                },
                "evidence_chain": [ev for ac in acceptance for ev in ac["evidence"]],
                "usage": None,
                "gaps": gaps,
            }
        )
    session_refs = [
        ref
        for item in items
        for attempt in item["attempts"]
        for ref in (attempt["parent_session_ref"], attempt["child_session_ref"])
        if isinstance(ref, str)
    ]
    sessions = (
        session_snapshot
        if session_snapshot is not None
        else read_session_evidence(session_refs)
    )
    sources["polylogue"] = _source(sessions, "polylogue")
    session_map = {
        row.get("ref"): row.get("evidence") for row in _rows(sessions.get("sessions"))
    }
    for item in items:
        for attempt in item["attempts"]:
            attempt["archive_observations"] = [
                {
                    "session_ref": ref,
                    "correlation_authority": "worker_claim",
                    "evidence_owner": "polylogue",
                    "evidence": session_map[ref],
                }
                for ref in (attempt["parent_session_ref"], attempt["child_session_ref"])
                if ref in session_map
            ]
    complete_scope = snapshot.get("complete") is True and all(
        ref in tasks for ref in bead_refs
    )
    counts = Counter(item["evidence_state"] for item in items)
    return {
        "schema_version": 1,
        "product": "campaign_evidence",
        "project": project,
        "bead_refs": list(dict.fromkeys(bead_refs)),
        "items": items,
        "temporal": {
            "requested_ref": snapshot.get("requested_ref", temporal.get("requested")),
            "resolved_ref": sources["beads"]["revision"],
            "observed_at": sources["beads"]["observed_at"],
            "event_time": snapshot.get("event_time", temporal.get("effective_at")),
            "known_at": temporal.get("known_at"),
            "watermark": sources["beads"]["watermark"],
            "refresh_id": None,
            "requested_refresh_id": refresh_id,
            "mode": "owner_snapshots",
            "evidence_time_basis": "event_time_bounded_retained_records"
            if historical
            else "current_retained_owner_records",
            "evidence_cutoff": cutoff.isoformat() if cutoff else None,
        },
        "sources": sources,
        "session_evidence": sessions.get("sessions", []),
        "counts": {
            "observed": len(items),
            "task_closed": sum(i["task_status"] == "closed" for i in items),
            "evidence_states": dict(counts),
            "scope_total": len(items) if complete_scope else None,
            "complete_scope": complete_scope,
        },
        "gaps": [gap for source in sources.values() for gap in source["gaps"]]
        + (
            [
                "Owner snapshots are independent observations, not the requested substrate generation"
            ]
            if refresh_id
            else []
        ),
    }


def campaign_scope_delta(
    baseline: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """Retain baseline obligations and distinguish only explicitly recorded changes."""
    before, after = _task_rows(baseline), _task_rows(target)
    complete = baseline.get("complete") is True and target.get("complete") is True
    provenance = _scope_provenance(target, {**before, **after})
    changes = []
    for ref in sorted(before.keys() | after.keys()):
        old, new = before.get(ref), after.get(ref)
        fields = _fields(new or {})
        metadata = _metadata(fields)
        explicit = metadata.get("scope_change")
        kind = "unchanged"
        if old is None:
            kind = (
                explicit
                if explicit in {"discovered_defect", "existing_scope_addition", "split"}
                else "addition_unknown"
            )
        elif new is None:
            kind = "removed" if complete else "missing_unknown"
        elif _fields(old).get("status") == "closed" and fields.get("status") not in {
            None,
            "closed",
        }:
            kind = "reopened"
        elif metadata.get("disposition") in {"superseded", "decomposed"}:
            kind = metadata["disposition"]
        elif _criteria(old) != _criteria(new) or _fields(old).get(
            "acceptance_criteria"
        ) != fields.get("acceptance_criteria"):
            kind = "acceptance_revised"
        created_at = _timestamp(fields.get("created_at"))
        baseline_time = _snapshot_time(baseline)
        creation = (
            (
                "created_since_baseline"
                if created_at > baseline_time
                else "existing_at_baseline"
            )
            if created_at and baseline_time
            else "unknown"
        )
        origins = [edge for edge in provenance if edge["from_ref"] == ref]
        successors = [edge for edge in provenance if edge["to_ref"] == ref]
        decomposition = [
            edge["from_ref"] or edge["from"]
            for edge in successors
            if edge["relation"] == "split_from"
        ]
        superseded_by = [
            edge["from_ref"] or edge["from"]
            for edge in successors
            if edge["relation"] == "supersedes"
        ]
        classification_by_relation = {
            "discovered_from": "discovered_defect"
            if fields.get("issue_type") == "bug"
            else "discovered_work",
            "split_from": "split",
            "supersedes": "replacement",
            "residual_of": "residual",
        }
        classifications = sorted(
            {classification_by_relation[edge["relation"]] for edge in origins}
        )
        if old is None and classifications:
            kind = (
                classifications[0] if len(classifications) == 1 else "provenance_mixed"
            )
        elif old is not None and superseded_by:
            kind = "superseded"
        elif old is not None and decomposition:
            kind = "decomposed"
        changes.append(
            {
                "bead_ref": ref,
                "kind": kind,
                "baseline_obligation": old is not None,
                "baseline": old,
                "target": new,
                "decomposition": decomposition or metadata.get("decomposed_into"),
                "decomposition_complete": None,
                "superseded_by": superseded_by,
                "provenance": origins + successors,
                "scope_classifications": classifications,
                "classification_source": "owner_relations"
                if classifications or successors
                else "metadata"
                if explicit
                else "unknown",
                "membership_change": "added"
                if old is None
                else "retained"
                if new is not None
                else "removed"
                if complete
                else "missing_unknown",
                "task_transition": "unknown"
                if old is None or new is None
                else "reopened"
                if _fields(old).get("status") == "closed"
                and fields.get("status") not in {None, "closed"}
                else "closed"
                if _fields(old).get("status") not in {None, "closed"}
                and fields.get("status") == "closed"
                else "unchanged",
                "origin": metadata.get("origin"),
                "creation": creation if old is None else None,
                "task_status": fields.get("status"),
                "role": _role(new or old or {}),
                "baseline_role": _role(old) if old else None,
            }
        )
    executable_roles_known = (
        all(
            change["role"] != "unknown" and change["baseline_role"] != "unknown"
            for change in changes
        )
        and not baseline.get("unknown_roles")
        and not target.get("unknown_roles")
    )
    baseline_executable = [
        change
        for change in changes
        if change["baseline_obligation"] and change["baseline_role"] == "executable"
    ]
    closed_discoveries = [
        change
        for change in changes
        if change["kind"] == "discovered_defect" and change["task_status"] == "closed"
    ]
    remaining = [
        change
        for change in changes
        if change["target"] is not None
        and change["role"] == "executable"
        and change["task_status"] not in {"closed", None}
    ]
    return {
        "schema_version": 1,
        "product": "campaign_scope_delta",
        "changes": changes,
        "baseline_source": _source(baseline, "beads"),
        "target_source": _source(target, "beads"),
        "provenance_edges": provenance,
        "provenance_coverage": target.get(
            "provenance_coverage", {"complete": False, "state": "unavailable"}
        ),
        "counts": {
            "baseline": len(before) if complete else None,
            "target": len(after) if complete else None,
            "by_kind": dict(Counter(change["kind"] for change in changes)),
            "complete_scope": complete,
            "baseline_executable": len(baseline_executable)
            if complete and executable_roles_known
            else None,
            "closed_discoveries_observed": len(closed_discoveries),
            "task_remaining": len(remaining)
            if complete
            and executable_roles_known
            and all(c["task_status"] is not None for c in changes if c["target"])
            else None,
            "evidence_remaining": None,
            **_leaf_counts(baseline, target),
        },
        "gaps": [
            "Task remaining is a status count; acceptance evidence must be joined separately"
        ]
        + (
            []
            if executable_roles_known
            else ["Executable versus structural roles are not completely declared"]
        )
        + (
            []
            if _object(target.get("provenance_coverage")).get("complete") is True
            else [
                "Scope provenance is incomplete; unclassified additions cannot establish discovery totals"
            ]
        ),
    }


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _role(task: dict[str, Any]) -> str:
    fields = _fields(task)
    declared = _metadata(fields).get("closure_role")
    if declared in {"work", "leaf"}:
        return "executable"
    if declared in {"executable", "gate", "decision", "structural"}:
        return str(declared)
    if fields.get("issue_type") in {"gate", "decision"}:
        return str(fields["issue_type"])
    return "unknown"


def _snapshot_time(snapshot: dict[str, Any]) -> datetime | None:
    temporal = _object(snapshot.get("temporal"))
    selected = _timestamp(temporal.get("requested")) or _timestamp(
        temporal.get("effective_at")
    )
    if selected is None and temporal.get("requested") in {
        None,
        "HEAD",
        "current",
        "latest",
        "now",
    }:
        selected = _timestamp(temporal.get("observed_at"))
    return selected


def _scope_provenance(
    snapshot: dict[str, Any], tasks: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    identities = {
        task.get("id", ref.rsplit("/", 1)[-1]): ref for ref, task in tasks.items()
    }

    def canonical(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        if value.startswith("sinnix://projects/") and "/beads/" in value:
            return value
        return identities.get(value)

    result = []
    for edge in _rows(snapshot.get("provenance_edges")):
        relation = edge.get("relation")
        if relation not in {
            "discovered_from",
            "split_from",
            "supersedes",
            "residual_of",
        }:
            continue
        result.append(
            {
                **edge,
                "from_ref": canonical(edge.get("from")),
                "to_ref": canonical(edge.get("to")),
            }
        )
    return result


def _leaf_refs(snapshot: dict[str, Any]) -> set[str] | None:
    tasks = _task_rows(snapshot)
    if (
        snapshot.get("complete") is not True
        or any(_role(task) == "unknown" for task in tasks.values())
        or snapshot.get("unknown_roles")
    ):
        return None
    executable = {ref for ref, task in tasks.items() if _role(task) == "executable"}
    declared = {
        ref
        for ref, task in tasks.items()
        if _metadata(_fields(task)).get("closure_role") == "leaf"
    }
    graph_leaves = snapshot.get("graph_leaves")
    if not isinstance(graph_leaves, list):
        return declared if declared == executable else None
    identities = {
        task.get("id", ref.rsplit("/", 1)[-1]): ref for ref, task in tasks.items()
    }
    graph_refs = {
        value if value in tasks else identities.get(value)
        for value in graph_leaves
        if isinstance(value, str)
    }
    if (
        None in graph_refs
        or len(graph_refs) != len(graph_leaves)
        or not declared.issubset(graph_refs)
    ):
        return None
    return {ref for ref in executable if ref in graph_refs}


def _leaf_counts(
    baseline: dict[str, Any], target: dict[str, Any]
) -> dict[str, int | None]:
    before, after = _task_rows(baseline), _task_rows(target)
    starting, current = _leaf_refs(baseline), _leaf_refs(target)
    baseline_status_known = starting is not None and all(
        _fields(before[ref]).get("status") is not None for ref in starting
    )
    target_status_known = (
        starting is not None
        and target.get("complete") is True
        and all(
            ref in after and _fields(after[ref]).get("status") is not None
            for ref in starting
        )
    )
    baseline_time, target_time = _snapshot_time(baseline), _snapshot_time(target)
    creation_times = {
        ref: _timestamp(_fields(after[ref]).get("created_at"))
        for ref in current or set()
    }
    created_count = None
    if (
        current is not None
        and starting is not None
        and baseline_time
        and target_time
        and baseline_time <= target_time
        and all(
            stamp is not None and stamp <= target_time
            for stamp in creation_times.values()
        )
    ):
        created_count = sum(
            stamp is not None and stamp > baseline_time
            for stamp in creation_times.values()
        )
    return {
        "baseline_closed_leaves": sum(
            _fields(before[ref]).get("status") == "closed" for ref in starting or set()
        )
        if baseline_status_known
        else None,
        "starting_leaves_closed_at_target": sum(
            _fields(after[ref]).get("status") == "closed" for ref in starting or set()
        )
        if target_status_known
        else None,
        "starting_open_leaves_closed_at_target": sum(
            _fields(before[ref]).get("status") != "closed"
            and _fields(after[ref]).get("status") == "closed"
            for ref in starting or set()
        )
        if baseline_status_known and target_status_known
        else None,
        "newly_created_leaves": created_count,
    }
