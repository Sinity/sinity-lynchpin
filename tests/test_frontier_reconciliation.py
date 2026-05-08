"""Tests for active_frontier_reconciliation (Arc C.4)."""

from __future__ import annotations

import json
from datetime import date

from lynchpin.analysis.frontier.reconciliation import (
    build_active_frontier_reconciliation,
)


def _frontier_payload(*projects):
    return {"projects": list(projects)}


def _project_frontier(*, project: str, items_by_lifecycle: dict):
    return {
        "project": project,
        "items_by_lifecycle": items_by_lifecycle,
    }


def _issue(number: int, lifecycle: str, *, state: str = "open",
           linked_packages: list | None = None, title: str = "t"):
    return {
        "kind": "issue",
        "number": number,
        "state": state,
        "lifecycle": lifecycle,
        "title": title,
        "url": f"https://gh/x/y/issues/{number}",
        "linked_packages": linked_packages or [],
        "lifecycle_confidence": 0.85,
        "inactivity_bucket": "active",
        "closed_at": None,
    }


def _work_payload(*projects):
    return {"projects": list(projects)}


def _project_packages(*, project: str, packages: list):
    return {"project": project, "packages": packages}


def _package(*, work_package_id: str, refs: dict, label: str = "feat"):
    return {"work_package_id": work_package_id, "refs": refs, "label": label}


def test_tracking_horizon_without_packages_is_flagged(tmp_path):
    frontier = _frontier_payload(_project_frontier(project="demo", items_by_lifecycle={
        "tracking_or_horizon": [_issue(1, "tracking_or_horizon", linked_packages=[])],
    }))
    work = _work_payload(_project_packages(project="demo", packages=[]))
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["tracking_count"] == 1
    assert payload["tracking_or_horizon_without_landed_packages"][0]["issue_ref"] == "issue#1"


def test_tracking_horizon_with_linked_package_is_not_flagged(tmp_path):
    frontier = _frontier_payload(_project_frontier(project="demo", items_by_lifecycle={
        "tracking_or_horizon": [_issue(1, "tracking_or_horizon", linked_packages=["wp:demo:pr:5"])],
    }))
    work = _work_payload(_project_packages(project="demo", packages=[]))
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["tracking_count"] == 0


def test_executed_without_package_is_flagged(tmp_path):
    frontier = _frontier_payload(_project_frontier(project="demo", items_by_lifecycle={
        "executed": [_issue(2, "executed", state="closed")],
    }))
    work = _work_payload(_project_packages(project="demo", packages=[]))
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["executed_without_package_count"] == 1
    assert "package-clustering miss" in payload["executed_without_work_package"][0]["interpretation"]


def test_executed_with_package_in_refs_is_not_flagged(tmp_path):
    frontier = _frontier_payload(_project_frontier(project="demo", items_by_lifecycle={
        "executed": [_issue(2, "executed", state="closed")],
    }))
    work = _work_payload(_project_packages(project="demo", packages=[
        _package(work_package_id="wp:demo:pr:5", refs={"issues": [2], "prs": [5]}),
    ]))
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["executed_without_package_count"] == 0


def test_orphan_refs_when_package_points_to_unknown_issue(tmp_path):
    frontier = _frontier_payload(_project_frontier(project="demo", items_by_lifecycle={
        "executed": [_issue(2, "executed", state="closed")],
    }))
    work = _work_payload(_project_packages(project="demo", packages=[
        # Package references issue #99 which doesn't exist in the frontier.
        _package(work_package_id="wp:demo:pr:5", refs={"issues": [99], "prs": [5]}),
    ]))
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["orphan_ref_package_count"] == 1
    orphan = payload["packages_with_orphan_refs"][0]
    assert orphan["stray_issue_refs"] == ["issue#99"]


def test_project_filter_isolates_selected_projects(tmp_path):
    frontier = _frontier_payload(
        _project_frontier(project="alpha", items_by_lifecycle={
            "tracking_or_horizon": [_issue(1, "tracking_or_horizon")],
        }),
        _project_frontier(project="beta", items_by_lifecycle={
            "tracking_or_horizon": [_issue(2, "tracking_or_horizon")],
        }),
    )
    work = _work_payload()
    f = tmp_path / "f.json"
    f.write_text(json.dumps(frontier))
    w = tmp_path / "w.json"
    w.write_text(json.dumps(work))

    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        projects=("alpha",),
        frontier_file=f, work_packages_file=w,
    )
    assert payload["summary"]["tracking_count"] == 1
    assert payload["projects"][0]["project"] == "alpha"


def test_empty_inputs_yield_empty_summary(tmp_path):
    payload = build_active_frontier_reconciliation(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        frontier_file="/nonexistent/f.json",
        work_packages_file="/nonexistent/w.json",
    )
    assert payload["summary"]["tracking_count"] == 0
    assert payload["summary"]["executed_without_package_count"] == 0
    assert payload["summary"]["orphan_ref_package_count"] == 0
    assert payload["projects"] == []
