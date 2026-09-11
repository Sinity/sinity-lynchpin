from copy import deepcopy

from lynchpin.analysis.projects.campaign_history import (
    project_trajectory,
    verification_regression,
)
from tests.analysis.test_campaign_evidence import REF, SHA, runtime


def test_verification_groups_show_ordered_outcome_changes_with_source_sha():
    runs = runtime()
    later = deepcopy(runs["rows"][0])
    later["run_id"] = "run-2"
    later["acceptance"]["recorded_at"] = "2026-01-02T10:00:00Z"
    later["workers"][0]["result"]["verification"][0].update(
        status="failed", tested_sha="c" * 40
    )
    runs["rows"].append(later)
    result = verification_regression(project="demo", runtime_snapshot=runs)
    group = result["groups"][0]
    assert group["bead_ref"] == REF
    assert group["ac_ids"] == ["AC-1"]
    assert group["transitions"][0]["kind"] == "reported_regression"
    assert group["transitions"][0]["after"]["outcome_authority"] == "worker_claim"
    assert group["transitions"][0]["before"]["tested_sha"] == SHA
    assert group["transitions"][0]["after"]["tested_sha"] == "c" * 40
    assert result["counts"]["complete_project_regressions"] is None


def test_different_acceptance_revision_is_not_compared_as_regression():
    runs = runtime()
    later = deepcopy(runs["rows"][0])
    later["workers"][0]["result"]["beads"][0]["bead_revision"] = "other-revision"
    runs["rows"].append(later)
    result = verification_regression(project="demo", runtime_snapshot=runs)
    assert len(result["groups"]) == 2
    assert result["counts"]["regressions_observed"] == 0


def test_missing_result_clocks_do_not_invent_order():
    runs = runtime()
    later = deepcopy(runs["rows"][0])
    later["acceptance"] = None
    later["workers"][0]["result"]["verification"][0]["status"] = "failed"
    runs["rows"].append(later)
    result = verification_regression(project="demo", runtime_snapshot=runs)
    assert result["groups"][0]["transitions"][0]["kind"] == "order_unknown"


def test_trajectory_counts_attempt_and_publication_once_for_shared_worker():
    runs = runtime()
    worker = runs["rows"][0]["workers"][0]
    worker["beads"].append("demo-2")
    result = project_trajectory(project="demo", runtime_snapshot=runs)
    assert result["counts"]["attempts_observed"] == 1
    assert result["counts"]["published_batches_observed"] == 1
    assert sum(row["published_batches_observed"] for row in result["rows"]) == 1
    assert result["complete_history"] is False
    assert result["counts"]["complete_project_throughput"] is None


def test_public_history_routes_return_real_rows(monkeypatch):
    from lynchpin.mcp.tools.public import lynchpin_project

    monkeypatch.setattr(
        "lynchpin.analysis.projects.campaign_history.read_batches",
        lambda project: runtime(),
    )
    trajectory = lynchpin_project(action="project_trajectory", project="demo")
    regression = lynchpin_project(action="verification_regression", project="demo")
    assert trajectory["data"]["rows"]
    assert regression["data"]["groups"]
    assert trajectory["data"]["outcome"] == "partial"
    assert regression["meta"]["effect_mode"] == "read"


def test_trajectory_reports_real_attempt_timestamp_basis():
    runs = runtime()
    worker = runs["rows"][0]["workers"][0]
    worker["attempts"] = [
        {
            "number": 1,
            "task_id": 7,
            "task_reference": "attempt-1",
            "recorded_at": "2026-01-02T11:00:00Z",
        }
    ]
    result = project_trajectory(project="demo", runtime_snapshot=runs)
    assert result["time_basis"]["attempts"] == "attempt_recorded_at"
    assert result["time_basis"]["attempt_basis_counts"] == {"attempt_recorded_at": 1}
    evidence = [
        record
        for row in result["rows"]
        for record in row["evidence"]
        if record["kind"] == "attempts_observed"
    ]
    assert evidence[0]["event_time"] == "2026-01-02T11:00:00Z"
    assert evidence[0]["event_time_basis"] == "attempt_recorded_at"
