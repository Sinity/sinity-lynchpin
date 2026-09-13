from __future__ import annotations

from copy import deepcopy

import pytest

from lynchpin.analysis.projects.campaign import campaign_evidence, campaign_scope_delta
from lynchpin.sources.campaign import (
    read_batches,
    read_native_evidence,
    read_session_evidence,
)


REF = "sinnix://projects/demo/beads/demo-1"
SHA = "a" * 40


def snapshot(*, status="closed", revision="task-v2"):
    return {
        "project_id": "demo",
        "task_revision": "dolt-snapshot-v3",
        "nodes": [
            {
                "ref": REF,
                "id": "demo-1",
                "status": status,
                "bead_revision": revision,
                "bead_revision_domain": "beads_row_revision",
                "bead_revision_coverage": "partial_update_coverage",
                "acceptance_criteria": [
                    {"id": "AC-1", "revision": revision, "text": "Behavior holds"}
                ],
            }
        ],
        "complete": True,
        "coverage": {"complete": True, "state": "complete"},
        "temporal": {
            "requested": "HEAD",
            "resolved_revision": "dolt-snapshot-v3",
            "observed_at": "2026-01-02T00:00:00Z",
            "watermark": None,
        },
    }


def runtime():
    return {
        "coverage": "retained_records",
        "revision": "runtime-v1",
        "gaps": [],
        "rows": [
            {
                "run_id": "run-1",
                "project": "demo",
                "created_at": "2026-01-01T00:00:00Z",
                "workers": [
                    {
                        "id": "demo-1",
                        "beads": ["demo-1"],
                        "bead_revisions": {"demo-1": "task-v2"},
                        "task_reference": "attempt-1",
                        "task_id": 7,
                        "result": {
                            "candidate_sha": SHA,
                            "beads": [
                                {
                                    "id": "demo-1",
                                    "bead_revision": "task-v2",
                                    "criteria": [
                                        {
                                            "ac_id": "AC-1",
                                            "text": "Behavior holds",
                                            "status": "satisfied",
                                            "evidence": "receipt",
                                        }
                                    ],
                                }
                            ],
                            "verification": [
                                {
                                    "command": "pytest test_behavior.py",
                                    "coverage": {
                                        "ac_ids": ["AC-1"],
                                        "scope": "focused",
                                    },
                                    "tested_sha": SHA,
                                    "status": "passed",
                                    "receipt": "agentctl://jobs/9",
                                }
                            ],
                        },
                    }
                ],
                "landing": {"candidate_sha": SHA},
                "acceptance": {
                    "candidate_sha": SHA,
                    "verify_run": {
                        "job_id": 9,
                        "candidate_sha": SHA,
                        "tested_sha": SHA,
                        "phase": "succeeded",
                        "git_dirty": False,
                    },
                    "recorded_at": "2026-01-01T10:00:00Z",
                    "published": {
                        "policy": "pr",
                        "candidate_sha": SHA,
                        "merge_commit": "b" * 40,
                    },
                },
            }
        ],
    }


def product(tasks=None, runs=None):
    return campaign_evidence(
        project="demo",
        bead_refs=[REF],
        task_snapshot=tasks or snapshot(),
        runtime_snapshot=runs if runs is not None else runtime(),
    )


def native_evidence():
    claim = deepcopy(runtime()["rows"][0]["workers"][0]["result"])
    claim["verification"][0]["receipt"] = "agentctl://jobs/9/verify-1"
    return {
        "coverage": "retained_native_evidence",
        "revision": "native-v1",
        "gaps": [],
        "rows": [
            {
                "schema_version": 1,
                "kind": "native_evidence",
                "evidence_id": "evidence-1",
                "recorded_at": "2026-01-01T10:00:00Z",
                "project": "demo",
                "worker_result": claim,
                "task_snapshot": [
                    {
                        "id": "demo-1",
                        "acceptance_criteria": [
                            {"id": "AC-1", "text": "Behavior holds"}
                        ],
                        "evidence_binding": {
                            "v2_available": True,
                            "bead_revision": "task-v2",
                            "criteria": [
                                {
                                    "id": "AC-1",
                                    "text": "Behavior holds",
                                }
                            ],
                        },
                    }
                ],
                "candidate": {
                    "candidate_sha": SHA,
                    "resolved_commit": SHA,
                    "current_head": SHA,
                    "current_dirty": False,
                    "checked": True,
                    "gaps": [],
                },
                "verification": [
                    {
                        "claim": claim["verification"][0],
                        "observation": {
                            "job_id": 9,
                            "reference": "verify-1",
                            "phase": "succeeded",
                            "exit_code": 0,
                            "tree_receipt": {"head": "c" * 40, "dirty": True},
                            "execution_receipt": {
                                "schema_version": 1,
                                "start": {
                                    "status": "observed",
                                    "head": SHA,
                                    "dirty": False,
                                },
                                "end": {
                                    "status": "observed",
                                    "head": SHA,
                                    "dirty": False,
                                },
                                "binding": "unchanged_endpoints",
                                "reason": None,
                            },
                            "result_kind": "succeeded",
                            "checked": True,
                            "eligible": True,
                            "gaps": [],
                        },
                    }
                ],
                "publication": {
                    "policy": "master",
                    "branch": "master",
                    "remote_head": "b" * 40,
                    "candidate_reachable": True,
                    "checked": True,
                    "state": "published",
                    "gaps": [],
                },
                "session_claims": {},
            }
        ],
    }


def native_product(tasks=None, evidence=None, runs=None):
    return campaign_evidence(
        project="demo",
        bead_refs=[REF],
        task_snapshot=tasks or snapshot(),
        runtime_snapshot=runs
        if runs is not None
        else {"rows": [], "coverage": "unavailable"},
        native_evidence_snapshot=evidence
        if evidence is not None
        else native_evidence(),
    )


def test_closed_task_is_not_completion_without_acceptance_evidence():
    result = product(runs={"rows": [], "coverage": "unavailable"})
    item = result["items"][0]
    assert item["task_status"] == "closed"
    assert item["evidence_state"] == "unknown"
    assert result["counts"]["task_closed"] == 1
    assert item["usage"] is None


def test_complete_chain_is_revision_and_integrated_sha_bound():
    result = product()
    item = result["items"][0]
    assert item["evidence_state"] == "verified"
    assert item["evidence_chain"][0]["integrated_sha"] == SHA
    assert item["evidence_chain"][0]["merge_commit"] == "b" * 40
    assert result["temporal"]["resolved_ref"] == "dolt-snapshot-v3"
    assert item["bead_revision"] == "task-v2"
    assert result["sources"]["beads"]["watermark"] is None


@pytest.mark.parametrize(
    "change",
    ["revision", "tested_sha", "missing_ac", "missing_receipt", "worker_sha_only"],
)
def test_landed_implementation_can_have_unproved_acceptance(change):
    runs = runtime()
    result = runs["rows"][0]["workers"][0]["result"]
    check = result["verification"][0]
    if change == "revision":
        result["beads"][0]["bead_revision"] = "old"
    elif change == "tested_sha":
        check["tested_sha"] = "c" * 40
    elif change == "missing_ac":
        check["coverage"]["ac_ids"] = []
    elif change == "missing_receipt":
        check["receipt"] = ""
    elif change == "worker_sha_only":
        del check["tested_sha"]
    item = product(runs=runs)["items"][0]
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"
    assert item["acceptance"][0]["state"] == "unknown"


def test_conflicting_acceptance_claims_are_retained():
    runs = runtime()
    other = deepcopy(runs["rows"][0])
    other["run_id"] = "run-2"
    other["workers"][0]["result"]["beads"][0]["criteria"][0]["status"] = "unsatisfied"
    runs["rows"].append(other)
    item = product(runs=runs)["items"][0]
    assert item["acceptance"][0]["state"] == "contradicted"
    assert len(item["evidence_chain"]) == 2


def test_legacy_text_criteria_cannot_be_completed_by_title_or_closure():
    tasks = snapshot()
    tasks["nodes"][0]["acceptance_criteria"] = "Behavior holds"
    tasks["nodes"][0]["title"] = "Done: successfully verified all acceptance criteria"
    item = product(tasks)["items"][0]
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"
    assert item["acceptance"] == []


def test_absent_attempt_is_unknown_even_when_batch_history_claims_complete():
    assert (
        product(runs={"rows": [], "coverage": "retained_records"})["items"][0][
            "evidence_state"
        ]
        == "unknown"
    )
    assert (
        product(runs={"rows": [], "coverage": "complete"})["items"][0]["evidence_state"]
        == "unknown"
    )


def test_scope_counts_require_complete_selected_snapshot():
    tasks = snapshot()
    tasks["complete"] = False
    result = product(tasks)
    assert result["counts"]["scope_total"] is None
    assert result["counts"]["observed"] == 1


def test_scope_delta_retains_decomposed_baseline_and_unclassified_additions():
    baseline, target = snapshot(), snapshot()
    target["nodes"][0]["metadata"] = {
        "disposition": "decomposed",
        "decomposed_into": [REF + "a"],
    }
    target["nodes"].append(
        {"ref": REF + "a", "id": "demo-1a", "title": "New defect found"}
    )
    target["nodes"].append(
        {
            "ref": REF + "b",
            "id": "demo-1b",
            "metadata": {"scope_change": "discovered_defect"},
        }
    )
    result = campaign_scope_delta(baseline, target)
    rows = {r["bead_ref"]: r for r in result["changes"]}
    assert rows[REF]["kind"] == "decomposed"
    assert rows[REF]["baseline_obligation"] is True
    assert rows[REF + "a"]["kind"] == "addition_unknown"
    assert rows[REF + "b"]["kind"] == "discovered_defect"


def test_missing_scope_member_is_not_retired_with_partial_coverage():
    target = {"nodes": [], "complete": False}
    result = campaign_scope_delta(snapshot(), target)
    assert result["changes"][0]["kind"] == "missing_unknown"
    assert result["counts"]["target"] is None


def test_public_campaign_route_consumes_structured_owner_records(monkeypatch):
    from lynchpin.mcp.tools.public import lynchpin_project

    monkeypatch.setattr(
        "lynchpin.analysis.projects.campaign.read_batches", lambda project: runtime()
    )
    monkeypatch.setattr("lynchpin.analysis.projects.owner_products.read_tasks", lambda *a, **kw: snapshot())
    monkeypatch.setattr("lynchpin.analysis.projects.campaign.read_native_evidence", lambda project: {"rows": [], "coverage": "unavailable", "gaps": []})
    result = lynchpin_project(
        action="campaign_evidence",
        project="demo",
        roots=[REF],
    )
    assert result["ok"] is True
    assert result["meta"]["effect_mode"] == "read"
    assert result["data"]["items"][0]["evidence_state"] == "verified"
    invalid = lynchpin_project(
        action="campaign_evidence", project="another", bead_refs=[REF]
    )
    assert invalid["ok"] is False
    assert invalid["error_code"] == "invalid_argument"


def test_public_scope_route_preserves_baseline(monkeypatch):
    from lynchpin.mcp.tools.public import lynchpin_project

    monkeypatch.setattr("lynchpin.analysis.projects.owner_products.read_tasks", lambda *a, **kw: snapshot())
    result = lynchpin_project(
        action="campaign_scope_delta",
        project="demo",
        roots=[REF],
        baseline="baseline-revision",
    )
    assert result["ok"] is True
    assert result["data"]["changes"][0]["baseline_obligation"] is True


def test_source_reads_public_batch_route_without_opening_runtime_files():
    calls = []

    def load(command):
        calls.append(command)
        return runtime()["rows"]

    result = read_batches("demo", loader=load)
    assert calls == [["agentctl", "batch", "list", "--project", "demo", "--json"]]
    assert result["coverage"] == "retained_records"
    assert result["revision"].startswith("sha256:")
    assert result["watermark"] is None


def test_source_contract_failure_is_not_empty_complete_history():
    result = read_batches("demo", loader=lambda command: {"error": "unavailable"})
    assert result["coverage"] == "unavailable"
    assert result["revision"] is None
    assert result["gaps"]


def test_source_reads_native_evidence_route_without_opening_runtime_files():
    calls = []

    def load(command):
        calls.append(command)
        return {
            "schema_version": 1,
            "owner": "agentctl",
            "interface": "agentctl.evidence.list",
            "project": "demo",
            "coverage": "retained_records",
            "records": native_evidence()["rows"],
            "gaps": [],
        }

    result = read_native_evidence("demo", loader=load)
    assert calls == [["agentctl", "evidence", "list", "--project", "demo", "--json"]]
    assert result["coverage"] == "retained_records"
    assert result["revision"].startswith("sha256:")


def test_native_evidence_can_prove_clean_published_verified_criterion():
    item = native_product()["items"][0]
    assert item["attempts"][0]["run_id"] is None
    assert item["attempts"][0]["source_kind"] == "native_evidence"
    assert item["evidence_state"] == "verified"
    assert item["implementation_landed"] is True
    assert item["publication"] == {"available": True, "state": "published"}
    assert item["verification"] == {"available": True, "state": "verified"}


def test_native_publication_is_independent_of_legacy_acceptance_text():
    tasks = snapshot()
    tasks["nodes"][0]["acceptance_criteria"] = "Behavior holds"
    item = native_product(tasks)["items"][0]
    assert item["acceptance"] == []
    assert item["implementation_landed"] is True
    assert item["publication"]["state"] == "published"
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"


@pytest.mark.parametrize("change", ["wrong_sha", "missing_receipt", "mismatched_start"])
def test_native_claims_need_exact_owner_receipts(change):
    evidence = native_evidence()
    claim = evidence["rows"][0]["verification"][0]["claim"]
    if change == "wrong_sha":
        claim["tested_sha"] = "c" * 40
    elif change == "missing_receipt":
        claim["receipt"] = ""
    else:
        evidence["rows"][0]["verification"][0]["observation"]["execution_receipt"][
            "start"
        ]["head"] = "c" * 40
    item = native_product(evidence=evidence)["items"][0]
    assert item["implementation_landed"] is True
    assert item["acceptance"][0]["state"] == "unknown"
    assert item["verification"]["state"] == "unknown"


def test_native_failed_receipt_is_observed_without_verifying_acceptance():
    evidence = native_evidence()
    claim = evidence["rows"][0]["verification"][0]["claim"]
    observation = evidence["rows"][0]["verification"][0]["observation"]
    claim["status"] = "failed"
    observation.update(phase="failed", exit_code=1, eligible=False)
    item = native_product(evidence=evidence)["items"][0]
    assert item["verification"] == {"available": True, "state": "failed"}
    assert item["acceptance"][0]["state"] != "verified"


def test_native_worker_model_is_a_claim_not_an_observation():
    evidence = native_evidence()
    evidence["rows"][0]["worker_result"]["actual_executor_model"] = "claimed-model"
    attempt = native_product(evidence=evidence)["items"][0]["attempts"][0]
    assert attempt["actual_executor_model"] is None
    assert attempt["worker_claim"]["actual_executor_model"] == "claimed-model"


def test_native_unrelated_project_is_rejected_not_silently_joined():
    rows = native_evidence()["rows"]
    rows[0]["project"] = "another"
    result = read_native_evidence(
        "demo",
        loader=lambda command: {
            "schema_version": 1,
            "owner": "agentctl",
            "interface": "agentctl.evidence.list",
            "project": "demo",
            "coverage": "retained_records",
            "records": rows,
            "gaps": [],
        },
    )
    assert result["coverage"] == "unavailable"
    assert result["rows"] == []


def test_failed_native_source_does_not_erase_batch_evidence():
    item = native_product(
        evidence={"rows": [], "coverage": "unavailable", "gaps": ["unavailable"]},
        runs=runtime(),
    )["items"][0]
    assert item["evidence_state"] == "verified"
    assert item["implementation_landed"] is True


def test_historical_scope_cannot_borrow_a_later_acceptance_result():
    tasks = snapshot()
    tasks["temporal"].update(
        requested="2026-01-01T09:00:00Z", effective_at="2026-01-01T08:00:00Z"
    )
    item = product(tasks)["items"][0]
    assert item["evidence_state"] == "attempted"
    assert item["implementation_landed"] is None
    assert item["acceptance"][0]["state"] == "unknown"
    assert item["evidence_chain"][0]["temporally_eligible"] is False
    assert item["verification"] == {"available": False, "state": "unknown"}
    assert item["publication"] == {"available": False, "state": "unknown"}


def test_historical_result_without_resolvable_clock_is_unknown():
    tasks = snapshot()
    tasks["temporal"]["requested"] = "old-revision"
    item = product(tasks)["items"][0]
    assert item["evidence_state"] == "unknown"
    assert item["implementation_landed"] is None


def test_historical_cutoff_excludes_future_attempts():
    tasks = snapshot()
    tasks["temporal"]["requested"] = "2025-12-31T00:00:00Z"
    item = product(tasks)["items"][0]
    assert item["attempts"] == []
    assert item["evidence_state"] == "unknown"


def test_duplicate_acceptance_ids_cannot_support_completion():
    tasks = snapshot()
    tasks["nodes"][0]["acceptance_criteria"].append(
        {"id": "AC-1", "text": "Another obligation"}
    )
    assert (
        product(tasks)["items"][0]["evidence_state"]
        == "implementation_landed_ac_incomplete"
    )


def test_scope_accounting_keeps_status_separate_and_requires_declared_roles():
    baseline, target = snapshot(status="open"), snapshot(status="open")
    baseline["temporal"]["effective_at"] = "2026-01-01T00:00:00Z"
    baseline["nodes"][0]["metadata"] = {"closure_role": "work"}
    target["nodes"][0]["metadata"] = {"closure_role": "work"}
    target["nodes"].append(
        {
            "ref": REF + "a",
            "created_at": "2025-01-01T00:00:00Z",
            "status": "closed",
            "metadata": {"scope_change": "discovered_defect", "closure_role": "work"},
        }
    )
    result = campaign_scope_delta(baseline, target)
    assert result["counts"]["baseline_executable"] == 1
    assert result["counts"]["closed_discoveries_observed"] == 1
    assert result["counts"]["task_remaining"] == 1
    assert result["counts"]["evidence_remaining"] is None
    assert result["changes"][1]["creation"] == "existing_at_baseline"


def test_session_source_consumes_declared_polylogue_facade_only():
    calls = []

    class Client:
        def get_session_orchestration(self, session_id):
            calls.append(session_id)
            return {
                "version": 1,
                "session_id": session_id,
                "usage": None,
                "coverage": {
                    "ingestion_watermark": {"scope": "session_primary_raw_revision"}
                },
                "gaps": ["archive_freshness_unverified"],
            }

    result = read_session_evidence(
        ["session:demo-session", "session:demo-session"], client=Client()
    )
    assert calls == ["demo-session"]
    assert result["coverage"] == "explicit_sessions"
    assert result["sessions"][0]["evidence"]["usage"] is None
    assert result["sessions"][0]["evidence"]["gaps"] == ["archive_freshness_unverified"]


def test_old_polylogue_without_stable_product_is_unavailable():
    result = read_session_evidence(["session:demo-session"], client=object())
    assert result["coverage"] == "unavailable"
    assert result["sessions"] == []
    assert result["gaps"]


def test_current_observations_do_not_claim_requested_substrate_generation():
    result = campaign_evidence(
        project="demo",
        bead_refs=[REF],
        task_snapshot=snapshot(),
        runtime_snapshot=runtime(),
        refresh_id="old-generation",
    )
    assert result["temporal"]["refresh_id"] is None
    assert result["temporal"]["requested_refresh_id"] == "old-generation"
    assert any("generation" in gap for gap in result["gaps"])


@pytest.mark.parametrize("action", ["project_trajectory", "verification_regression"])
def test_unavailable_historical_products_do_not_fabricate_series(action, monkeypatch):
    from lynchpin.mcp.tools.public import lynchpin_project

    monkeypatch.setattr(
        "lynchpin.analysis.projects.campaign_history.read_batches",
        lambda project: {"rows": [], "coverage": "unavailable"},
    )
    result = lynchpin_project(action=action, project="demo")
    assert result["ok"] is True
    assert result["data"]["outcome"] == "unavailable"


def test_failed_receipt_contradicts_satisfied_claim_on_same_sha():
    runs = runtime()
    checks = runs["rows"][0]["workers"][0]["result"]["verification"]
    checks.append({**deepcopy(checks[0]), "status": "failed"})
    item = product(runs=runs)["items"][0]
    assert item["acceptance"][0]["state"] == "contradicted"
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"


def test_conflicting_github_observation_does_not_prove_publication():
    runs = runtime()
    runs["rows"][0]["landing"]["pr"] = {"state": "OPEN", "number": 5}
    item = product(runs=runs)["items"][0]
    assert item["implementation_landed"] is None
    assert item["evidence_state"] == "attempted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("git_dirty", True),
        ("git_dirty", None),
        ("tested_sha", "c" * 40),
        ("job_id", 10),
        ("requested_sha", "c" * 40),
    ],
)
def test_worker_claim_requires_clean_sha_and_receipt_corrobation(field, value):
    runs = runtime()
    runs["rows"][0]["acceptance"]["verify_run"][field] = value
    item = product(runs=runs)["items"][0]
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"
    assert item["acceptance"][0]["state"] == "unknown"


def test_all_recorded_resumes_survive_without_inventing_prior_results():
    runs = runtime()
    worker = runs["rows"][0]["workers"][0]
    worker["attempts"] = [
        {
            "number": 1,
            "task_reference": "attempt-old",
            "task_id": 6,
            "model": "requested-old",
        },
        {
            "number": 2,
            "task_reference": "attempt-1",
            "task_id": 7,
            "model": "requested-new",
        },
    ]
    worker["result"].update(
        actual_executor_model="claimed-model",
        actual_executor_observed_by="runner",
        measured_usage={"total_tokens": 100},
    )
    attempts = product(runs=runs)["items"][0]["attempts"]
    assert len(attempts) == 2
    assert [a["planned_model"] for a in attempts] == ["requested-old", "requested-new"]
    assert [a["result_available"] for a in attempts] == [False, True]
    assert attempts[0]["verification"] == []
    assert attempts[0]["usage"] is None
    assert attempts[1]["actual_executor_model"] is None
    assert attempts[1]["usage"] is None
    assert attempts[1]["worker_claim"]["actual_executor_model"] == "claimed-model"


@pytest.mark.parametrize(
    "relation,issue_type,expected",
    [
        ("discovered_from", "bug", "discovered_defect"),
        ("discovered_from", "task", "discovered_work"),
        ("split_from", "task", "split"),
        ("supersedes", "task", "replacement"),
        ("residual_of", "task", "residual"),
    ],
)
def test_scope_consumes_owner_provenance_without_expanding_membership(
    relation, issue_type, expected
):
    baseline, target = snapshot(status="open"), snapshot(status="open")
    target["nodes"].append(
        {"ref": REF + "a", "id": "demo-1a", "status": "open", "issue_type": issue_type}
    )
    target["provenance_edges"] = [
        {
            "from": "demo-1a",
            "to": "demo-1",
            "relation": relation,
            "native_relation": relation.replace("_", "-"),
        },
        {
            "from": "outside",
            "to": "demo-1a",
            "relation": "discovered_from",
            "native_relation": "discovered-from",
        },
    ]
    target["provenance_coverage"] = {
        "complete": True,
        "state": "complete",
        "frontier": [],
    }
    result = campaign_scope_delta(baseline, target)
    rows = {row["bead_ref"]: row for row in result["changes"]}
    assert set(rows) == {REF, REF + "a"}
    assert rows[REF + "a"]["kind"] == expected
    assert rows[REF + "a"]["classification_source"] == "owner_relations"
    assert rows[REF]["baseline_obligation"] is True
    assert result["provenance_edges"][1]["from_ref"] is None
    if relation == "split_from":
        assert rows[REF]["kind"] == "decomposed"
        assert rows[REF]["decomposition"] == [REF + "a"]
        assert rows[REF]["decomposition_complete"] is None
    if relation == "supersedes":
        assert rows[REF]["kind"] == "superseded"


def test_gateway_metadata_split_edge_preserves_original_obligation():
    baseline, target = snapshot(status="open"), snapshot(status="open")
    target["nodes"].append({"id": "demo-1a", "ref": REF + "a", "status": "open"})
    edge = {
        "from": "demo-1a",
        "to": "demo-1",
        "relation": "split_from",
        "native_relation": None,
        "metadata_key": "split_from",
        "source": "issue_metadata",
        "target_kind": "bead",
    }
    target["provenance_edges"] = [edge]
    target["provenance_coverage"] = {"complete": True, "state": "complete"}

    result = campaign_scope_delta(baseline, target)
    rows = {row["bead_ref"]: row for row in result["changes"]}

    assert set(rows) == {REF, REF + "a"}
    assert rows[REF]["kind"] == "decomposed"
    assert rows[REF]["baseline_obligation"] is True
    assert rows[REF]["decomposition"] == [REF + "a"]
    assert rows[REF]["decomposition_complete"] is None
    assert rows[REF + "a"]["kind"] == "split"
    assert rows[REF + "a"]["classification_source"] == "owner_relations"
    assert result["provenance_edges"] == [
        {**edge, "from_ref": REF + "a", "to_ref": REF}
    ]


def test_leaf_status_and_creation_counts_keep_baseline_and_target_denominators():
    baseline, target = snapshot(), snapshot()
    baseline["nodes"][0]["metadata"] = '{"closure_role":"leaf"}'
    baseline["nodes"].append(
        {
            "id": "demo-1b",
            "ref": REF + "b",
            "status": "open",
            "metadata": {"closure_role": "leaf"},
        }
    )
    baseline["graph_leaves"] = ["demo-1", "demo-1b"]
    baseline["temporal"].update(
        requested="2026-01-01T00:00:00Z", effective_at="2025-12-30T00:00:00Z"
    )
    target["nodes"] = deepcopy(baseline["nodes"])
    target["nodes"][1]["status"] = "closed"
    target["nodes"].append(
        {
            "id": "demo-1c",
            "ref": REF + "c",
            "status": "open",
            "created_at": "2026-01-01T12:00:00Z",
            "metadata": {"closure_role": "leaf"},
        }
    )
    for row in target["nodes"][:2]:
        row["created_at"] = "2025-12-31T00:00:00Z"
    target["graph_leaves"] = ["demo-1", "demo-1b", "demo-1c"]
    result = campaign_scope_delta(baseline, target)
    assert result["counts"]["baseline_closed_leaves"] == 1
    assert result["counts"]["starting_leaves_closed_at_target"] == 2
    assert result["counts"]["starting_open_leaves_closed_at_target"] == 1
    assert result["counts"]["newly_created_leaves"] == 1
    target["nodes"][2].pop("created_at")
    assert (
        campaign_scope_delta(baseline, target)["counts"]["newly_created_leaves"] is None
    )
    target["complete"] = False
    partial = campaign_scope_delta(baseline, target)
    assert partial["counts"]["baseline_closed_leaves"] == 1
    assert partial["counts"]["starting_leaves_closed_at_target"] is None


def test_unknown_roles_cannot_supply_exact_leaf_counts():
    baseline, target = snapshot(), snapshot()
    baseline["graph_leaves"] = ["demo-1"]
    target["graph_leaves"] = ["demo-1"]
    counts = campaign_scope_delta(baseline, target)["counts"]
    assert counts["baseline_closed_leaves"] is None
    assert counts["newly_created_leaves"] is None


@pytest.mark.parametrize("row_revision", [7123456789012345678, "7123456789012345678"])
def test_gateway_row_revision_joins_without_using_distinct_dolt_revision(row_revision):
    tasks = snapshot(revision=row_revision)
    node = tasks["nodes"][0]
    node["acceptance_criteria"] = "Behavior holds"
    node["metadata"] = {
        "acceptance_criteria": [{"id": "AC-1", "text": "Behavior holds"}]
    }
    runs = runtime()
    worker = runs["rows"][0]["workers"][0]
    worker["bead_revisions"]["demo-1"] = str(row_revision)
    worker["result"]["beads"][0]["bead_revision"] = str(row_revision)
    result = product(tasks, runs)
    item = result["items"][0]
    assert item["evidence_state"] == "verified"
    assert item["task_revision"] == "dolt-snapshot-v3"
    assert item["bead_revision"] == "7123456789012345678"
    assert item["acceptance"][0]["revision_domain"] == "beads_row_revision"
    assert result["sources"]["beads"]["revision"] == "dolt-snapshot-v3"


def test_absent_row_revision_never_falls_back_to_matching_snapshot_hash():
    tasks = snapshot()
    tasks["nodes"][0].pop("bead_revision")
    tasks["task_revision"] = "task-v2"
    tasks["temporal"]["resolved_revision"] = "task-v2"
    item = product(tasks)["items"][0]
    assert item["task_revision"] == "task-v2"
    assert item["bead_revision"] is None
    assert item["acceptance"][0]["state"] == "unknown"
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"


def test_equal_revision_text_in_another_domain_cannot_verify_acceptance():
    tasks = snapshot()
    tasks["nodes"][0]["bead_revision_domain"] = "dolt_commit"
    assert product(tasks)["items"][0]["acceptance"][0]["state"] == "unknown"


def test_separate_acceptance_version_does_not_replace_owner_row_revision():
    tasks = snapshot()
    tasks["nodes"][0]["acceptance_criteria"][0]["revision"] = "acceptance-definition-v1"
    item = product(tasks)["items"][0]
    assert item["evidence_state"] == "verified"
    assert item["acceptance"][0]["acceptance_revision"] == "acceptance-definition-v1"
    assert item["acceptance"][0]["revision"] == "task-v2"


@pytest.mark.parametrize("text", [None, "The changed obligation must hold"])
def test_equal_row_revision_cannot_hide_missing_or_changed_acceptance_text(text):
    tasks = snapshot()
    tasks["nodes"][0]["acceptance_criteria"][0]["text"] = text
    item = product(tasks)["items"][0]
    assert item["bead_revision"] == "task-v2"
    assert item["evidence_state"] == "implementation_landed_ac_incomplete"
    evidence = item["acceptance"][0]["evidence"][0]
    assert evidence["same_revision"] is True
    assert evidence["same_acceptance_content"] is False
    assert evidence["selected_acceptance_text"] == text
    assert evidence["claimed_acceptance_text"] == "Behavior holds"


def test_claim_without_acceptance_text_cannot_be_verified_by_row_token():
    runs = runtime()
    runs["rows"][0]["workers"][0]["result"]["beads"][0]["criteria"][0].pop("text")
    assert product(runs=runs)["items"][0]["acceptance"][0]["state"] == "unknown"
