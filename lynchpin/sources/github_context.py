"""Canonical GitHub lifecycle context product."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from .github import (
    GitHubActor,
    GitHubComment,
    GitHubItem,
    GitHubLabel,
    GitHubReview,
    GitHubReviewComment,
)


GITHUB_CONTEXT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class GitHubContextRow:
    project: str
    item: GitHubItem


def github_context_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "github/context.ndjson"


def github_context_manifest_path(root: Path | None = None) -> Path:
    return github_context_path(root).with_suffix(".manifest.json")


def iter_github_context(
    path: Path | None = None,
    *,
    projects: set[str] | None = None,
    window: tuple[date, date] | None = None,
    ensure: bool = True,
) -> Iterator[GitHubContextRow]:
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("github_context", window=window)
    target = path or github_context_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical GitHub context materialization is missing: {target}. "
            "Run python -m lynchpin.analysis refresh."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            project = str(payload.get("project") or "")
            if projects and project not in projects:
                continue
            item = github_item_from_payload(payload)
            if item is not None:
                yield GitHubContextRow(project=project, item=item)


def github_item_from_payload(payload: dict[str, object]) -> GitHubItem | None:
    number = _int(payload.get("number"))
    if number == 0:
        return None
    kind = "pr" if payload.get("kind") == "pr" else "issue"
    raw_state = str(payload.get("state") or "unknown").lower()
    state = raw_state if raw_state in {"open", "closed", "merged"} else "unknown"
    return GitHubItem(
        repo=str(payload.get("repo") or payload.get("project") or ""),
        slug=str(payload.get("slug") or ""),
        kind=kind,
        number=number,
        title=str(payload.get("title") or ""),
        state=state,
        url=str(payload.get("url")) if payload.get("url") else None,
        author=GitHubActor(str(payload.get("author") or "") or None),
        labels=tuple(GitHubLabel(str(label)) for label in payload.get("labels") or ()),
        body=str(payload.get("body") or ""),
        comments=tuple(_comment(row) for row in payload.get("comments") or () if isinstance(row, dict)),
        created_at=_dt(payload.get("created_at")),
        updated_at=_dt(payload.get("updated_at")),
        closed_at=_dt(payload.get("closed_at")),
        merged_at=_dt(payload.get("merged_at")),
        merge_commit=str(payload.get("merge_commit")) if payload.get("merge_commit") else None,
        review_decision=str(payload.get("review_decision")) if payload.get("review_decision") else None,
        reviews=tuple(_review(row) for row in payload.get("reviews") or () if isinstance(row, dict)),
        latest_reviews=tuple(_review(row) for row in payload.get("latest_reviews") or () if isinstance(row, dict)),
        review_comments=tuple(_review_comment(row) for row in payload.get("review_comments") or () if isinstance(row, dict)),
    )


def github_item_to_payload(*, project: str, item: GitHubItem) -> dict[str, Any]:
    return {
        "project": project,
        "repo": item.repo,
        "slug": item.slug,
        "kind": item.kind,
        "number": item.number,
        "title": item.title,
        "state": item.state,
        "url": item.url,
        "author": item.author.login,
        "labels": [label.name for label in item.labels],
        "body": item.body,
        "comments": [
            {
                "author": comment.author.login,
                "body": comment.body,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
                "url": comment.url,
            }
            for comment in item.comments
        ],
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at else None,
        "merged_at": item.merged_at.isoformat() if item.merged_at else None,
        "merge_commit": item.merge_commit,
        "review_decision": item.review_decision,
        "reviews": [
            {
                "author": review.author.login,
                "state": review.state,
                "body": review.body,
                "submitted_at": review.submitted_at.isoformat() if review.submitted_at else None,
                "url": review.url,
            }
            for review in item.reviews
        ],
        "latest_reviews": [
            {
                "author": review.author.login,
                "state": review.state,
                "body": review.body,
                "submitted_at": review.submitted_at.isoformat() if review.submitted_at else None,
                "url": review.url,
            }
            for review in item.latest_reviews
        ],
        "review_comments": [
            {
                "author": comment.author.login,
                "body": comment.body,
                "path": comment.path,
                "line": comment.line,
                "diff_hunk": comment.diff_hunk,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
                "url": comment.url,
                "review_id": comment.review_id,
            }
            for comment in item.review_comments
        ],
        "review_count": len(item.reviews),
        "latest_review_count": len(item.latest_reviews),
        "review_comment_count": len(item.review_comments),
    }


def _comment(payload: dict[str, object]) -> GitHubComment:
    return GitHubComment(
        author=GitHubActor(str(payload.get("author") or "") or None),
        body=str(payload.get("body") or ""),
        created_at=_dt(payload.get("created_at")),
        url=str(payload.get("url")) if payload.get("url") else None,
    )


def _review(payload: dict[str, object]) -> GitHubReview:
    return GitHubReview(
        author=GitHubActor(str(payload.get("author") or "") or None),
        state=str(payload.get("state") or ""),
        body=str(payload.get("body") or ""),
        submitted_at=_dt(payload.get("submitted_at")),
        url=str(payload.get("url")) if payload.get("url") else None,
    )


def _review_comment(payload: dict[str, object]) -> GitHubReviewComment:
    return GitHubReviewComment(
        author=GitHubActor(str(payload.get("author") or "") or None),
        body=str(payload.get("body") or ""),
        path=str(payload.get("path")) if payload.get("path") else None,
        line=_int_or_none(payload.get("line")),
        diff_hunk=str(payload.get("diff_hunk")) if payload.get("diff_hunk") else None,
        created_at=_dt(payload.get("created_at")),
        url=str(payload.get("url")) if payload.get("url") else None,
        review_id=_int_or_none(payload.get("review_id")),
    )


def _dt(value: object):
    from ..core.parse import parse_datetime

    return parse_datetime(value) if isinstance(value, str) and value else None


def _int(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
