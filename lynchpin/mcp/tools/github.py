"""MCP tools for querying normalized GitHub issues and PRs from the substrate.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time; PEP 563 string annotations cause TypeError.
"""

from typing import Any

from lynchpin.mcp.server import app


@app.tool()
def list_github_issues(
    project: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """List GitHub issues from the substrate, optionally filtered by project and/or state.

    Args:
        project: Project name (e.g. "sinex", "sinity-lynchpin"). If omitted, all projects.
        state: Issue state filter: "open" or "closed". If omitted, all states.

    Returns issues with number, title, body, labels, author, state, comment_count,
    created_at, closed_at, and url. Does not include comment bodies — use
    get_github_issue() for full content including comments.
    """
    from lynchpin.mcp.tools._utils import json_safe as _json_safe
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.github import iter_github_issues

    issues: list[dict[str, Any]] = []
    try:
        with connect(read_only=True) as conn:
            for row in iter_github_issues(conn, project=project, state=state):
                issues.append(_json_safe(row))
    except Exception as exc:
        return {"error": str(exc), "issues": []}

    return {
        "project_filter": project,
        "state_filter": state,
        "total": len(issues),
        "issues": issues,
    }


@app.tool()
def get_github_issue(project: str, number: int) -> dict[str, Any]:
    """Return a full GitHub issue including title, body, labels, and all comments.

    Args:
        project: Project name (e.g. "sinex").
        number: Issue number.
    """
    from lynchpin.mcp.tools._utils import json_safe as _json_safe
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.github import (
        get_github_issue as _get_issue,
        iter_github_issue_comments,
    )

    try:
        with connect(read_only=True) as conn:
            issue = _get_issue(conn, project, number)
            if issue is None:
                return {"error": f"Issue {project}#{number} not found in substrate"}
            comments = list(iter_github_issue_comments(conn, project, number))
    except Exception as exc:
        return {"error": str(exc)}

    return _json_safe({
        **issue,
        "comments": [_json_safe(c) for c in comments],
    })


@app.tool()
def list_github_prs(
    project: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """List GitHub PRs from the substrate, optionally filtered by project and/or state.

    Args:
        project: Project name (e.g. "sinex"). If omitted, all projects.
        state: PR state filter: "open", "closed", or "merged". If omitted, all states.

    Returns PRs with number, title, state, author, labels, merge_commit, review_decision,
    comment_count, review_count, review_comment_count, created_at, merged_at, and url.
    The merge_commit SHA can be JOINed to commit_fact.sha to find the squash-merge commit.
    For full content (body, comments, reviews), use get_github_pr().
    """
    from lynchpin.mcp.tools._utils import json_safe as _json_safe
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.github import iter_github_prs

    prs: list[dict[str, Any]] = []
    try:
        with connect(read_only=True) as conn:
            for row in iter_github_prs(conn, project=project, state=state):
                prs.append(_json_safe(row))
    except Exception as exc:
        return {"error": str(exc), "prs": []}

    return {
        "project_filter": project,
        "state_filter": state,
        "total": len(prs),
        "prs": prs,
    }


@app.tool()
def get_github_pr(project: str, number: int) -> dict[str, Any]:
    """Return a full GitHub PR including title, body, labels, comments, reviews, and review comments.

    The merge_commit field contains the squash-merge SHA and can be JOINed to
    commit_fact.sha to link this PR to the commit that introduced it.

    Args:
        project: Project name (e.g. "sinex").
        number: PR number.
    """
    from lynchpin.mcp.tools._utils import json_safe as _json_safe
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.github import (
        get_github_pr as _get_pr,
        iter_github_pr_comments,
        iter_github_pr_review_comments,
        iter_github_pr_reviews,
    )

    try:
        with connect(read_only=True) as conn:
            pr = _get_pr(conn, project, number)
            if pr is None:
                return {"error": f"PR {project}#{number} not found in substrate"}
            comments = list(iter_github_pr_comments(conn, project, number))
            reviews = list(iter_github_pr_reviews(conn, project, number))
            review_comments = list(iter_github_pr_review_comments(conn, project, number))
    except Exception as exc:
        return {"error": str(exc)}

    return _json_safe({
        **pr,
        "comments": [_json_safe(c) for c in comments],
        "reviews": [_json_safe(r) for r in reviews],
        "review_comments": [_json_safe(rc) for rc in review_comments],
    })
