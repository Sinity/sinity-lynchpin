import json
import subprocess
from pathlib import Path

from lynchpin.sources.github import (
    classify_lifecycle,
    extract_commit_refs,
    extract_issue_refs,
    fetch_pr,
    fetch_issues,
    fetch_pr_review_comments,
    lifecycle_summary,
    slug_from_remote,
)


def _completed(args, cwd, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)


def test_slug_from_remote_supports_ssh_and_https():
    assert slug_from_remote("git@github.com:Sinity/polylogue.git") == "Sinity/polylogue"
    assert slug_from_remote("https://github.com/Sinity/sinex.git") == "Sinity/sinex"


def test_extract_issue_refs_from_commit_subject():
    assert extract_issue_refs("fix(cli): handle state (#846)") == (846,)
    assert extract_issue_refs("fix: closes #12 and refs #15") == (12, 15)
    assert extract_commit_refs("feat: add thing (#5)\n\nRefs #7") == {"prs": {5}, "issues": {7}}


def test_fetch_issues_parses_comments_and_classifies_lifecycle(tmp_path: Path):
    (tmp_path / ".git").mkdir()

    payload = [
        {
            "number": 1,
            "title": "tracking: daemon convergence",
            "state": "OPEN",
            "url": "https://github.com/Sinity/polylogue/issues/1",
            "body": "Tracking spine for the architecture.",
            "labels": [{"name": "tracking"}],
            "author": {"login": "Sinity"},
            "comments": [],
            "createdAt": "2026-05-01T00:00:00Z",
            "updatedAt": "2026-05-02T00:00:00Z",
        },
        {
            "number": 2,
            "title": "old port issue",
            "state": "CLOSED",
            "body": "Retired as stale; folded into #5.",
            "labels": [],
            "author": {"login": "Sinity"},
            "comments": [{"author": {"login": "Sinity"}, "body": "Superseded by newer issue.", "createdAt": "2026-05-03T00:00:00Z"}],
            "createdAt": "2026-05-01T00:00:00Z",
            "updatedAt": "2026-05-03T00:00:00Z",
            "closedAt": "2026-05-03T00:00:00Z",
        },
    ]

    def runner(args, cwd):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return _completed(args, cwd, stdout="git@github.com:Sinity/polylogue.git\n")
        assert args[:3] == ["gh", "issue", "list"]
        return _completed(args, cwd, stdout=json.dumps(payload))

    result = fetch_issues(tmp_path, runner=runner)

    assert result.status == "ok"
    assert result.slug == "Sinity/polylogue"
    assert len(result.items) == 2
    assert result.items[1].comments[0].author.login == "Sinity"
    assert classify_lifecycle(result.items[0]).lifecycle == "tracking_or_horizon"
    assert classify_lifecycle(result.items[1]).lifecycle == "folded_or_consolidated"
    assert lifecycle_summary(result.items) == {"tracking_or_horizon": 1, "folded_or_consolidated": 1}


def test_fetch_issues_uses_cache_for_real_gh_calls(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    cache_dir = tmp_path / "cache"
    payload = [
        {
            "number": 3,
            "title": "cached issue",
            "state": "OPEN",
            "labels": [],
            "comments": [],
        }
    ]
    calls = []

    Config = type("Config", (), {"cache_dir": cache_dir})

    def fake_run(args, cwd=None):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return _completed(args, cwd, stdout="git@github.com:Sinity/polylogue.git\n")
        calls.append(tuple(args))
        return _completed(args, cwd, stdout=json.dumps(payload))

    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: Config())
    monkeypatch.setattr("lynchpin.sources.github._run", fake_run)

    first = fetch_issues(tmp_path)
    second = fetch_issues(tmp_path)

    assert first.status == "ok"
    assert second.status == "ok"
    assert len(calls) == 1


def test_fetch_pr_parses_reviews_and_inline_review_comments(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    pr_payload = {
        "number": 5,
        "title": "Refactor analysis",
        "state": "OPEN",
        "url": "https://github.com/Sinity/lynchpin/pull/5",
        "body": "Needs review.",
        "labels": [],
        "author": {"login": "Sinity"},
        "comments": [],
        "createdAt": "2026-05-01T00:00:00Z",
        "updatedAt": "2026-05-02T00:00:00Z",
        "reviewDecision": "CHANGES_REQUESTED",
        "reviews": [
            {
                "author": {"login": "reviewer"},
                "state": "CHANGES_REQUESTED",
                "body": "Please simplify this path.",
                "submittedAt": "2026-05-02T00:00:00Z",
                "url": "https://github.com/Sinity/lynchpin/pull/5#pullrequestreview-1",
            }
        ],
        "latestReviews": [
            {
                "author": {"login": "reviewer"},
                "state": "CHANGES_REQUESTED",
                "body": "Still needs work.",
                "submittedAt": "2026-05-03T00:00:00Z",
            }
        ],
    }
    inline_payload = [
        {
            "user": {"login": "reviewer"},
            "body": "This branch duplicates the other path.",
            "path": "lynchpin/analysis/cli.py",
            "line": 42,
            "diff_hunk": "@@",
            "created_at": "2026-05-02T01:00:00Z",
            "html_url": "https://github.com/Sinity/lynchpin/pull/5#discussion_r1",
            "pull_request_review_id": 1,
        }
    ]

    def runner(args, cwd):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return _completed(args, cwd, stdout="git@github.com:Sinity/lynchpin.git\n")
        if args[:3] == ["gh", "pr", "view"]:
            return _completed(args, cwd, stdout=json.dumps(pr_payload))
        if args[:2] == ["gh", "api"]:
            return _completed(args, cwd, stdout=json.dumps(inline_payload))
        raise AssertionError(args)

    item = fetch_pr(tmp_path, 5, runner=runner, include_review_comments=True)

    assert item is not None
    assert item.review_decision == "CHANGES_REQUESTED"
    assert item.reviews[0].author.login == "reviewer"
    assert item.latest_reviews[0].body == "Still needs work."
    assert item.review_comments[0].path == "lynchpin/analysis/cli.py"
    assert item.review_comments[0].line == 42
    assert classify_lifecycle(item).lifecycle == "open_frontier"


def test_fetch_pr_review_comments_returns_empty_on_bad_payload(tmp_path: Path):
    (tmp_path / ".git").mkdir()

    def runner(args, cwd):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return _completed(args, cwd, stdout="git@github.com:Sinity/lynchpin.git\n")
        return _completed(args, cwd, stdout="{}")

    assert fetch_pr_review_comments(tmp_path, 5, runner=runner) == ()
