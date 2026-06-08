from __future__ import annotations

from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.github import (
    get_github_issue,
    get_github_pr,
    iter_github_issue_comments,
    iter_github_issues,
    iter_github_pr_comments,
    iter_github_pr_review_comments,
    iter_github_pr_reviews,
    iter_github_prs,
    promote_github_issue_comments,
    promote_github_issues,
    promote_github_pr_comments,
    promote_github_pr_review_comments,
    promote_github_pr_reviews,
    promote_github_prs,
)


def _make_conn(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
    return db


def test_promote_and_read_issues(tmp_path):
    db = _make_conn(tmp_path)
    issues = [
        {
            "project": "sinex",
            "number": 1,
            "title": "Bug report",
            "body": "Something is broken",
            "state": "open",
            "author": "Sinity",
            "labels": ["bug"],
            "comment_count": 1,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-02T00:00:00+00:00",
            "closed_at": None,
            "url": "https://github.com/Sinity/sinex/issues/1",
        },
        {
            "project": "sinex",
            "number": 2,
            "title": "Feature request",
            "body": "",
            "state": "closed",
            "author": "Sinity",
            "labels": [],
            "comment_count": 0,
            "created_at": "2026-06-03T00:00:00+00:00",
            "updated_at": "2026-06-04T00:00:00+00:00",
            "closed_at": "2026-06-04T00:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/issues/2",
        },
    ]
    comments = [
        {
            "project": "sinex",
            "issue_number": 1,
            "comment_idx": 0,
            "author": "contributor",
            "body": "I can reproduce",
            "created_at": "2026-06-01T12:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/issues/1#issuecomment-1",
        }
    ]
    with connect(db) as conn:
        assert promote_github_issues(conn, rows=issues) == 2
        assert promote_github_issue_comments(conn, rows=comments) == 1
        # Idempotent — re-promote replaces
        assert promote_github_issues(conn, rows=issues[:1]) == 1
        all_issues = list(iter_github_issues(conn))
        assert len(all_issues) == 1
        open_issues = list(iter_github_issues(conn, state="open"))
        assert len(open_issues) == 1
        assert open_issues[0]["title"] == "Bug report"
        issue = get_github_issue(conn, "sinex", 1)
        assert issue is not None
        assert issue["number"] == 1
        coms = list(iter_github_issue_comments(conn, "sinex", 1))
        assert len(coms) == 1
        assert coms[0]["body"] == "I can reproduce"


def test_promote_and_read_prs(tmp_path):
    db = _make_conn(tmp_path)
    prs = [
        {
            "project": "sinex",
            "number": 100,
            "title": "feat: add X",
            "body": "Description",
            "state": "merged",
            "author": "Sinity",
            "labels": [],
            "merge_commit": "abc123def456abc123def456abc123def456abc1",
            "review_decision": "APPROVED",
            "comment_count": 1,
            "review_count": 1,
            "review_comment_count": 1,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-02T00:00:00+00:00",
            "closed_at": "2026-06-02T00:00:00+00:00",
            "merged_at": "2026-06-02T00:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/pull/100",
        }
    ]
    pr_comments = [
        {
            "project": "sinex",
            "pr_number": 100,
            "comment_idx": 0,
            "author": "reviewer",
            "body": "LGTM",
            "created_at": "2026-06-02T00:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/pull/100#issuecomment-1",
        }
    ]
    pr_reviews = [
        {
            "project": "sinex",
            "pr_number": 100,
            "review_idx": 0,
            "author": "reviewer",
            "state": "APPROVED",
            "body": "Looks good",
            "submitted_at": "2026-06-02T00:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/pull/100#pullrequestreview-1",
        }
    ]
    pr_review_comments = [
        {
            "project": "sinex",
            "pr_number": 100,
            "comment_idx": 0,
            "author": "reviewer",
            "body": "Nit: rename this",
            "path": "src/main.rs",
            "line": 42,
            "diff_hunk": "@@ -40,3 +40,3 @@",
            "created_at": "2026-06-02T00:00:00+00:00",
            "url": "https://github.com/Sinity/sinex/pull/100#discussion-1",
        }
    ]
    with connect(db) as conn:
        assert promote_github_prs(conn, rows=prs) == 1
        assert promote_github_pr_comments(conn, rows=pr_comments) == 1
        assert promote_github_pr_reviews(conn, rows=pr_reviews) == 1
        assert promote_github_pr_review_comments(conn, rows=pr_review_comments) == 1

        merged = list(iter_github_prs(conn, state="merged"))
        assert len(merged) == 1
        assert merged[0]["merge_commit"] == "abc123def456abc123def456abc123def456abc1"

        pr = get_github_pr(conn, "sinex", 100)
        assert pr is not None
        assert pr["review_decision"] == "APPROVED"

        coms = list(iter_github_pr_comments(conn, "sinex", 100))
        assert coms[0]["body"] == "LGTM"

        reviews = list(iter_github_pr_reviews(conn, "sinex", 100))
        assert reviews[0]["state"] == "APPROVED"

        rcs = list(iter_github_pr_review_comments(conn, "sinex", 100))
        assert rcs[0]["path"] == "src/main.rs"
        assert rcs[0]["line"] == 42


def test_get_github_issue_returns_none_for_missing(tmp_path):
    db = _make_conn(tmp_path)
    with connect(db) as conn:
        assert get_github_issue(conn, "nonexistent", 999) is None
        assert get_github_pr(conn, "nonexistent", 999) is None
