"""Tests for git_analysis MCP tools."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


_UTC = timezone.utc


def _fake_repos(names=("lynchpin", "sinex")):
    from lynchpin.sources.git import RepoInfo

    return [
        RepoInfo(
            name=n,
            path=f"/realm/project/{n}",
            exists=True,
            branch="master",
            head=f"abc{i}",
            last_commit_at=datetime(2026, 5, 1, 12, 0, tzinfo=_UTC),
        )
        for i, n in enumerate(names)
    ]


def test_repo_names_returns_list_of_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.sources.git as _git_src

    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    from lynchpin.mcp.tools.git_analysis import repo_names

    result = repo_names()
    assert len(result) == 2
    assert {r["name"] for r in result} == {"lynchpin", "sinex"}
    assert result[0]["exists"] is True


def test_repo_language_stats_unknown_repo_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.sources.git as _git_src

    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    from lynchpin.mcp.tools.git_analysis import repo_language_stats

    result = repo_language_stats(repo="nonexistent")
    assert "error" in result
    assert "known_repos" in result
    assert "nonexistent" in result["error"]


def test_repo_language_stats_unavailable_when_no_report(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.sources.git as _git_src

    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    monkeypatch.setattr(_git_src, "repo_tokei", lambda repo_name: None)
    from lynchpin.mcp.tools.git_analysis import repo_language_stats

    result = repo_language_stats(repo="lynchpin")
    assert result["status"] == "unavailable"
    assert result["repo"] == "lynchpin"


def test_repo_language_stats_returns_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources.git import TokeiReport, TokeiLanguageStat
    import lynchpin.sources.git as _git_src

    report = TokeiReport(
        repo="lynchpin",
        total_code=5000,
        total_lines=6500,
        languages=[TokeiLanguageStat(language="Python", code=5000, comments=300, blanks=1200)],
    )
    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    monkeypatch.setattr(_git_src, "repo_tokei", lambda repo_name: report)
    from lynchpin.mcp.tools.git_analysis import repo_language_stats

    result = repo_language_stats(repo="lynchpin")
    assert result["repo"] == "lynchpin"
    assert result["total_code"] == 5000
    assert len(result["languages"]) == 1
    assert result["languages"][0]["language"] == "Python"


def test_repo_file_list_unknown_repo_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.sources.git as _git_src

    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    from lynchpin.mcp.tools.git_analysis import repo_file_list

    result = repo_file_list(repo="nonexistent")
    assert "error" in result
    assert result["files"] == []


def test_repo_file_list_returns_files(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources.git import RepoFile
    import lynchpin.sources.git as _git_src

    fake_files = [
        RepoFile(repo="lynchpin", relative=f"src/{i}.py", absolute=f"/repo/src/{i}.py", category="source")
        for i in range(5)
    ]
    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    monkeypatch.setattr(_git_src, "repo_files", lambda repo_name, tracked_only=True: iter(fake_files))
    from lynchpin.mcp.tools.git_analysis import repo_file_list

    result = repo_file_list(repo="lynchpin", limit=3)
    assert result["repo"] == "lynchpin"
    assert result["file_count"] == 3
    assert result["truncated"] is True
    assert result["files"][0]["category"] == "source"


def test_repo_recent_commits_unknown_repo_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.sources.git as _git_src

    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    from lynchpin.mcp.tools.git_analysis import repo_recent_commits

    result = repo_recent_commits(repo="nonexistent")
    assert "error" in result
    assert result["commits"] == []


def test_repo_recent_commits_returns_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources.git import RepoCommitSummary
    import lynchpin.sources.git as _git_src

    fake_commits = [
        RepoCommitSummary(
            repo="lynchpin",
            sha=f"sha{i}",
            author="Sinity",
            authored_at=datetime(2026, 5, i + 1, 12, 0, tzinfo=_UTC),
            subject=f"feat: change {i}",
        )
        for i in range(5)
    ]
    monkeypatch.setattr(_git_src, "repos", lambda names=None: _fake_repos())
    monkeypatch.setattr(_git_src, "recent_commits", lambda repo_name, limit=20: fake_commits)
    from lynchpin.mcp.tools.git_analysis import repo_recent_commits

    result = repo_recent_commits(repo="lynchpin", limit=10)
    assert result["repo"] == "lynchpin"
    assert result["commit_count"] == 5
    assert result["commits"][0]["sha"] == "sha0"
    assert result["commits"][0]["subject"] == "feat: change 0"
