"""MCP tools for direct git source analysis (repo structure, language stats, recent commits).

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.tools._utils import json_safe as _json_safe


def _known_repo_names() -> list[str]:
    from lynchpin.sources.git import repos

    return [r.name for r in repos()]


def _validate_repo(repo: str) -> str | None:
    """Return an error string if repo is unknown, else None."""
    known = _known_repo_names()
    if repo not in known:
        return f"Unknown repo {repo!r}. Known repos: {known}"
    return None


def repo_names() -> list[dict[str, Any]]:
    """List all known git repositories with their paths and branch info."""
    from dataclasses import asdict
    from lynchpin.sources.git import repos

    return [_json_safe(asdict(r)) for r in repos()]


def repo_language_stats(repo: str) -> dict[str, Any]:
    """Language breakdown for a repository via tokei.

    Returns per-language code/comment/blank counts and totals.
    Returns {"status": "unavailable"} when tokei is not installed.
    Returns {"error": ..., "known_repos": [...]} for unknown repo names.

    Parameters:
        repo: Repository name (e.g. "sinity-lynchpin").
    """
    from dataclasses import asdict
    from lynchpin.sources.git import repo_tokei

    err = _validate_repo(repo)
    if err:
        return {"error": err, "known_repos": _known_repo_names()}

    report = repo_tokei(repo)
    if report is None:
        return {"status": "unavailable", "repo": repo, "reason": "tokei not installed or repo not found"}

    d = asdict(report)
    return _json_safe(d)


def repo_file_list(
    repo: str,
    tracked_only: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """List files tracked in a repository.

    Parameters:
        repo:         Repository name.
        tracked_only: If True (default), only list git-tracked files.
        limit:        Maximum number of files to return (default 500).
    """
    from lynchpin.sources.git import repo_files

    err = _validate_repo(repo)
    if err:
        return {"error": err, "known_repos": _known_repo_names(), "files": []}

    effective_limit = min(max(limit, 1), 5000)
    files = []
    for f in repo_files(repo, tracked_only=tracked_only):
        if len(files) >= effective_limit:
            break
        files.append({
            "relative": f.relative,
            "category": f.category,
        })

    return {
        "repo": repo,
        "tracked_only": tracked_only,
        "file_count": len(files),
        "truncated": len(files) == effective_limit,
        "files": files,
    }


def repo_recent_commits(
    repo: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent commit summaries for a repository.

    Parameters:
        repo:  Repository name.
        limit: Maximum number of commits to return (default 20, max 200).
    """
    from lynchpin.sources.git import recent_commits

    err = _validate_repo(repo)
    if err:
        return {"error": err, "known_repos": _known_repo_names(), "commits": []}

    effective_limit = min(max(limit, 1), 200)
    commits = recent_commits(repo, limit=effective_limit)
    return {
        "repo": repo,
        "commit_count": len(commits),
        "commits": [
            _json_safe({
                "sha": c.sha,
                "author": c.author,
                "authored_at": c.authored_at,
                "subject": c.subject,
            })
            for c in commits
        ],
    }


__all__ = ["repo_names", "repo_language_stats", "repo_file_list", "repo_recent_commits"]
