"""Tests for frontier closure SLOs (M.9)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.graph.closure_slos import compute_closure_slos, render_closure_slos
from lynchpin.graph.issue_closure_chain import IssueClosureChain

UTC = timezone.utc


def _chain(
    *,
    project: str,
    issue_ref: str = "issue#1",
    state: str = "closed",
    lifecycle: str = "executed",
    opened_days_ago: int | None = 30,
    closed_days_ago: int | None = 0,
    status: str = "complete",
    reference: date = date(2026, 5, 7),
) -> IssueClosureChain:
    opened_at = (
        datetime.combine(reference - timedelta(days=opened_days_ago), datetime.min.time(), tzinfo=UTC)
        if opened_days_ago is not None else None
    )
    closed_at = (
        datetime.combine(reference - timedelta(days=closed_days_ago), datetime.min.time(), tzinfo=UTC)
        if closed_days_ago is not None else None
    )
    return IssueClosureChain(
        project=project,
        issue_ref=issue_ref,
        issue_state=state,
        issue_lifecycle=lifecycle,
        opened_at=opened_at,
        closed_at=closed_at,
        linked_pr_refs=("pr#5",) if status == "complete" else (),
        closing_commit_shas=("abc",) if status == "complete" else (),
        closure_status=status,  # type: ignore[arg-type]
        evidence_node_ids=(),
        caveats=(),
    )


def test_median_p75_p90_for_completed_chains():
    ref = date(2026, 5, 7)
    # Five completed chains with durations: 10, 20, 30, 40, 50 days
    chains = [
        _chain(project="demo", issue_ref=f"issue#{i}",
               opened_days_ago=days, closed_days_ago=0, reference=ref)
        for i, days in enumerate([10, 20, 30, 40, 50], start=1)
    ]
    slos = compute_closure_slos(chains, reference=ref)
    row = slos.projects[0]
    assert row.completed_count == 5
    assert row.median_days_to_close == 30
    # p75 idx = round(0.75 * 4) = 3 → values[3] = 40
    assert row.p75_days_to_close == 40
    # p90 idx = round(0.9 * 4) = 4 → values[4] = 50
    assert row.p90_days_to_close == 50


def test_broken_orphaned_partial_counted_separately():
    chains = [
        _chain(project="demo", issue_ref="issue#1", status="broken",
               opened_days_ago=20, closed_days_ago=10, lifecycle="open_frontier"),
        _chain(project="demo", issue_ref="issue#2", status="orphaned",
               opened_days_ago=15, closed_days_ago=5, lifecycle="retired"),
        _chain(project="demo", issue_ref="issue#3", status="partial",
               opened_days_ago=10, closed_days_ago=None, lifecycle="open_frontier"),
    ]
    slos = compute_closure_slos(chains)
    row = slos.projects[0]
    assert row.broken_count == 1
    assert row.orphaned_count == 1
    assert row.partial_count == 1
    assert row.completed_count == 0


def test_stale_tracking_counts_open_horizon_issues_past_threshold():
    ref = date(2026, 5, 7)
    chains = [
        # Stale: open tracking issue 120 days old (> 90d threshold)
        _chain(project="demo", issue_ref="issue#1", state="open",
               lifecycle="tracking_or_horizon",
               opened_days_ago=120, closed_days_ago=None, status="partial",
               reference=ref),
        # Recent tracking — under threshold
        _chain(project="demo", issue_ref="issue#2", state="open",
               lifecycle="tracking_or_horizon",
               opened_days_ago=30, closed_days_ago=None, status="partial",
               reference=ref),
        # Open frontier (not tracking) — shouldn't count
        _chain(project="demo", issue_ref="issue#3", state="open",
               lifecycle="open_frontier",
               opened_days_ago=120, closed_days_ago=None, status="partial",
               reference=ref),
    ]
    slos = compute_closure_slos(chains, reference=ref, stale_tracking_days=90)
    row = slos.projects[0]
    assert row.stale_tracking_count == 1


def test_render_includes_overall_summary_and_violations():
    chains = [
        _chain(project="demo", issue_ref="issue#1", status="complete",
               opened_days_ago=20, closed_days_ago=0),
        _chain(project="demo", issue_ref="issue#2", status="broken",
               opened_days_ago=10, closed_days_ago=5, lifecycle="open_frontier"),
    ]
    slos = compute_closure_slos(chains)
    rendered = render_closure_slos(slos)
    assert "1 completed" in rendered
    assert "1 broken" in rendered
    assert "demo" in rendered
    # Broken count rendered with bold for emphasis
    assert "**1**" in rendered


def test_empty_chains_yields_empty_message():
    slos = compute_closure_slos(())
    rendered = render_closure_slos(slos)
    assert "No closure chains" in rendered


def test_violation_density_orders_projects():
    ref = date(2026, 5, 7)
    chains = [
        # Project A: 2 broken
        _chain(project="alpha", issue_ref="issue#1", status="broken",
               opened_days_ago=10, closed_days_ago=5, lifecycle="open_frontier", reference=ref),
        _chain(project="alpha", issue_ref="issue#2", status="broken",
               opened_days_ago=10, closed_days_ago=5, lifecycle="open_frontier", reference=ref),
        # Project B: 1 broken
        _chain(project="beta", issue_ref="issue#3", status="broken",
               opened_days_ago=10, closed_days_ago=5, lifecycle="open_frontier", reference=ref),
    ]
    slos = compute_closure_slos(chains, reference=ref)
    rendered = render_closure_slos(slos)
    # alpha has more violations → appears before beta in the table
    alpha_pos = rendered.find("alpha")
    beta_pos = rendered.find("beta")
    assert alpha_pos != -1 and beta_pos != -1
    assert alpha_pos < beta_pos
