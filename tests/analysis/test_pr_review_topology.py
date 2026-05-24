"""Tests for PR review topology (M.7)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from lynchpin.analysis.frontier.pr_review_topology import (
    build_active_pr_review_topology,
)
from lynchpin.sources.github import (
    GitHubActor,
    GitHubItem,
    GitHubReview,
    GitHubReviewComment,
)

UTC = timezone.utc


def _pr(
    *,
    number: int,
    title: str = "feat: x",
    state: str = "open",
    author: str = "alice",
    created: datetime | None = None,
    closed: datetime | None = None,
    merged: datetime | None = None,
    reviews: list[GitHubReview] = None,
    review_comments: list[GitHubReviewComment] = None,
    review_decision: str | None = None,
) -> GitHubItem:
    created = created or datetime(2026, 5, 1, 10, tzinfo=UTC)
    return GitHubItem(
        repo="demo",
        slug="acme/demo",
        kind="pr",
        number=number,
        title=title,
        state=state,
        url=f"https://github.com/acme/demo/pull/{number}",
        author=GitHubActor(login=author),
        labels=(),
        body="",
        comments=(),
        created_at=created,
        updated_at=closed or merged or created,
        closed_at=closed,
        merged_at=merged,
        review_decision=review_decision,
        reviews=tuple(reviews or ()),
        latest_reviews=tuple(reviews or ()),
        review_comments=tuple(review_comments or ()),
    )


def _review(*, login: str, state: str, hours_after: int = 1,
            base: datetime | None = None) -> GitHubReview:
    base = base or datetime(2026, 5, 1, 10, tzinfo=UTC)
    return GitHubReview(
        author=GitHubActor(login=login),
        state=state,
        body="",
        submitted_at=base + timedelta(hours=hours_after),
    )


def _review_comment(*, path: str = "src/foo.py", line: int = 10) -> GitHubReviewComment:
    return GitHubReviewComment(
        author=GitHubActor(login="bob"),
        body="nit",
        path=path,
        line=line,
        diff_hunk="@@",
        created_at=datetime(2026, 5, 1, 11, tzinfo=UTC),
    )


def test_basic_pr_with_one_review_summary():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr = _pr(
        number=1, state="closed",
        created=base, merged=base + timedelta(hours=4),
        reviews=[_review(login="bob", state="APPROVED", hours_after=1, base=base)],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    assert payload["summary"]["pr_count"] == 1
    row = payload["prs"][0]
    assert row["review_count"] == 1
    assert row["review_decisions"] == ["APPROVED"]
    assert row["approval_count"] == 1
    assert row["reviewers"] == ["bob"]
    assert row["time_to_first_review_minutes"] == 60.0
    assert row["time_to_merge_minutes"] == 240.0
    assert row["final_decision"] == "merged"
    assert row["friction_signals"] == []


def test_changes_requested_then_merged_is_friction_signal():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr = _pr(
        number=2, state="closed",
        created=base, merged=base + timedelta(hours=8),
        reviews=[
            _review(login="bob", state="CHANGES_REQUESTED", hours_after=1, base=base),
            _review(login="bob", state="APPROVED",         hours_after=6, base=base),
        ],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert row["review_round_count"] == 2
    assert row["changes_requested_count"] == 1
    assert "changes_requested_then_merged" in row["friction_signals"]


def test_self_merge_no_review_friction_signals():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr = _pr(
        number=3, state="closed",
        created=base, merged=base + timedelta(hours=1),
        reviews=[],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert "no_review_at_merge" in row["friction_signals"]
    assert "self_merge" in row["friction_signals"]


def test_long_to_first_review_signal():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    # First review 4 days after PR opened → ≥3-day threshold breached.
    pr = _pr(
        number=4, state="open",
        created=base,
        reviews=[_review(login="bob", state="COMMENTED", hours_after=4 * 24, base=base)],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert "long_to_first_review" in row["friction_signals"]


def test_many_rounds_signal_when_four_plus_review_submissions():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr = _pr(
        number=5, state="closed",
        created=base, merged=base + timedelta(days=1),
        reviews=[
            _review(login="bob",   state="CHANGES_REQUESTED", hours_after=1, base=base),
            _review(login="bob",   state="CHANGES_REQUESTED", hours_after=4, base=base),
            _review(login="carol", state="COMMENTED",         hours_after=8, base=base),
            _review(login="bob",   state="APPROVED",          hours_after=20, base=base),
        ],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert row["review_round_count"] == 4
    assert "many_rounds" in row["friction_signals"]


def test_review_comment_storm_signal():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr = _pr(
        number=6, state="open", created=base,
        review_comments=[_review_comment() for _ in range(25)],
    )
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert row["review_comment_count"] == 25
    assert "review_comment_storm" in row["friction_signals"]


def test_stale_open_signal_for_old_pr_with_no_reviews():
    base = datetime(2026, 4, 1, 10, tzinfo=UTC)  # 36 days before reference
    pr = _pr(number=7, state="open", created=base, reviews=[])
    payload = build_active_pr_review_topology(
        items=[("demo", [pr])],
        start=date(2026, 4, 1), end=date(2026, 5, 7),
    )
    row = payload["prs"][0]
    assert "stale_open" in row["friction_signals"]


def test_per_project_slo_aggregates_median_p75():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    prs = [
        _pr(
            number=10 + i, state="closed",
            created=base, merged=base + timedelta(hours=hrs),
            reviews=[_review(login="bob", state="APPROVED", hours_after=1, base=base)],
        )
        for i, hrs in enumerate([2, 4, 6, 8, 10])  # merge times: 2h, 4h, 6h, 8h, 10h
    ]
    payload = build_active_pr_review_topology(
        items=[("demo", prs)],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    slo = payload["projects"][0]
    assert slo["pr_count"] == 5
    assert slo["merged_pr_count"] == 5
    # median time to merge = 6h = 360 min
    assert slo["median_time_to_merge_minutes"] == 360.0
    # p75 idx = round(0.75 * 4) = 3 → values[3] = 8h = 480 min
    assert slo["p75_time_to_merge_minutes"] == 480.0


def test_review_round_distribution_per_project():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    prs = [
        _pr(number=20, state="closed", created=base, merged=base + timedelta(hours=2),
            reviews=[_review(login="bob", state="APPROVED", hours_after=1, base=base)]),
        _pr(number=21, state="closed", created=base, merged=base + timedelta(hours=2),
            reviews=[_review(login="bob", state="APPROVED", hours_after=1, base=base)]),
        _pr(number=22, state="closed", created=base, merged=base + timedelta(hours=8),
            reviews=[
                _review(login="bob", state="CHANGES_REQUESTED", hours_after=1, base=base),
                _review(login="bob", state="APPROVED",          hours_after=6, base=base),
            ]),
    ]
    payload = build_active_pr_review_topology(
        items=[("demo", prs)],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    distribution = payload["projects"][0]["review_round_distribution"]
    # 2 PRs with 1 round, 1 PR with 2 rounds
    assert distribution.get("1") == 2 or distribution.get(1) == 2
    assert distribution.get("2") == 1 or distribution.get(2) == 1


def test_project_filter_isolates_selected():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    pr_alpha = _pr(number=1, state="open", created=base)
    pr_beta = _pr(number=2, state="open", created=base)
    payload = build_active_pr_review_topology(
        items=[("alpha", [pr_alpha]), ("beta", [pr_beta])],
        projects=["alpha"],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    assert payload["summary"]["pr_count"] == 1
    assert payload["prs"][0]["project"] == "alpha"


def test_empty_items_yields_zero_summary():
    payload = build_active_pr_review_topology(
        items=[],
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    assert payload["summary"]["pr_count"] == 0
    assert payload["prs"] == []
    assert payload["projects"] == []


def test_missing_project_snapshot_is_not_treated_as_empty(tmp_path):
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_pr_review_topology(
            snapshot_file=tmp_path / "missing.json",
            start=date(2026, 5, 1),
            end=date(2026, 5, 7),
        )
