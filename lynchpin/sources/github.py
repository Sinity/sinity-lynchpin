"""GitHub source: typed issue/PR/comment facts and lifecycle classification.

The module is intentionally a thin `gh` CLI adapter. It performs no writes and
keeps failures explicit so higher-level analysis can decide whether GitHub
evidence is unavailable, degraded, or complete.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..core.parse import parse_datetime

GitHubItemKind = Literal["issue", "pr"]
GitHubItemState = Literal["open", "closed", "merged", "unknown"]
GitHubLifecycle = Literal[
    "executed",
    "pr_closed",
    "retired_stale",
    "folded_or_consolidated",
    "tracking_or_horizon",
    "misframed",
    "open_frontier",
    "unclear",
]

_BODY_FIELDS = "body,comments,labels,number,state,title,url,author,createdAt,updatedAt,closedAt"
_PR_FIELDS = (
    "body,comments,labels,number,state,title,url,author,createdAt,updatedAt,"
    "closedAt,mergedAt,mergeCommit,reviewDecision,reviews,latestReviews"
)
_ISSUE_REF_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|ref(?:s)?)\s+#(\d+)", re.I)
_PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)")
_CONSOLIDATED_RE = re.compile(r"\b(folded|consolidated|superseded|replaced|absorbed|umbrella|duplicate)\b", re.I)
_RETIRED_RE = re.compile(r"\b(retired|stale|obsolete|no longer|not doing|wontfix|won't fix)\b", re.I)
_TRACKING_RE = re.compile(r"\b(tracking|horizon|umbrella|roadmap|spine|epic|meta)\b", re.I)
_MISFRAMED_RE = re.compile(r"\b(misframed|wrong premise|wrong shape|not the right|reframed)\b", re.I)
_EXECUTED_RE = re.compile(r"\b(done|implemented|landed|shipped|completed|fixed|merged)\b", re.I)


@dataclass(frozen=True)
class GitHubActor:
    login: str | None


@dataclass(frozen=True)
class GitHubLabel:
    name: str


@dataclass(frozen=True)
class GitHubComment:
    author: GitHubActor
    body: str
    created_at: datetime | None
    url: str | None = None


@dataclass(frozen=True)
class GitHubReview:
    author: GitHubActor
    state: str
    body: str
    submitted_at: datetime | None
    url: str | None = None


@dataclass(frozen=True)
class GitHubReviewComment:
    author: GitHubActor
    body: str
    path: str | None
    line: int | None
    diff_hunk: str | None
    created_at: datetime | None
    url: str | None = None
    review_id: int | None = None


@dataclass(frozen=True)
class GitHubItem:
    repo: str
    slug: str
    kind: GitHubItemKind
    number: int
    title: str
    state: GitHubItemState
    url: str | None
    author: GitHubActor
    labels: tuple[GitHubLabel, ...]
    body: str
    comments: tuple[GitHubComment, ...]
    created_at: datetime | None
    updated_at: datetime | None
    closed_at: datetime | None
    merged_at: datetime | None = None
    merge_commit: str | None = None
    review_decision: str | None = None
    reviews: tuple[GitHubReview, ...] = ()
    latest_reviews: tuple[GitHubReview, ...] = ()
    review_comments: tuple[GitHubReviewComment, ...] = ()


@dataclass(frozen=True)
class GitHubLifecycleClassification:
    lifecycle: GitHubLifecycle
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class GitHubFetchResult:
    status: Literal["ok", "unavailable", "error"]
    repo: str
    slug: str | None
    items: tuple[GitHubItem, ...]
    reason: str | None = None


CommandRunner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]
GITHUB_CACHE_TTL_SECONDS = 48 * 60 * 60


def repo_slug(repo_path: Path) -> str | None:
    """Return `owner/name` for a local GitHub checkout, if origin is GitHub."""
    if not (repo_path / ".git").exists():
        return None
    result = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    if result.returncode != 0:
        return None
    return slug_from_remote(result.stdout.strip())


def slug_from_remote(remote: str) -> str | None:
    patterns = (
        r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/.]+)(?:\.git)?$",
        r"https?://github\.com/(?P<owner>[^/]+)/(?P<name>[^/.]+)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, remote)
        if match:
            return f"{match.group('owner')}/{match.group('name')}"
    return None


def fetch_issues(
    repo_path: Path,
    *,
    state: Literal["open", "closed", "all"] = "open",
    limit: int = 100,
    runner: CommandRunner | None = None,
    use_cache: bool = True,
) -> GitHubFetchResult:
    """Fetch issues with comments for a local repo."""
    return _fetch_items(repo_path, kind="issue", state=state, limit=limit, runner=runner, use_cache=use_cache)


def fetch_prs(
    repo_path: Path,
    *,
    state: Literal["open", "closed", "merged", "all"] = "open",
    limit: int = 100,
    runner: CommandRunner | None = None,
    use_cache: bool = True,
) -> GitHubFetchResult:
    """Fetch PRs with comments for a local repo."""
    return _fetch_items(repo_path, kind="pr", state=state, limit=limit, runner=runner, use_cache=use_cache)


def fetch_issue(
    repo_path: Path,
    number: int,
    *,
    runner: CommandRunner | None = None,
    use_cache: bool = True,
    max_age_seconds: int = GITHUB_CACHE_TTL_SECONDS,
) -> GitHubItem | None:
    """Fetch one issue by number."""
    return _fetch_item(
        repo_path,
        kind="issue",
        number=number,
        runner=runner,
        use_cache=use_cache,
        max_age_seconds=max_age_seconds,
    )


def cached_issue(repo_path: Path, number: int) -> GitHubItem | None:
    """Return one issue from the local GitHub cache without invoking ``gh``."""
    return _cached_item(repo_path, kind="issue", number=number)


def fetch_pr(
    repo_path: Path,
    number: int,
    *,
    runner: CommandRunner | None = None,
    use_cache: bool = True,
    include_review_comments: bool = False,
    max_age_seconds: int = GITHUB_CACHE_TTL_SECONDS,
) -> GitHubItem | None:
    """Fetch one PR by number."""
    item = _fetch_item(
        repo_path,
        kind="pr",
        number=number,
        runner=runner,
        use_cache=use_cache,
        max_age_seconds=max_age_seconds,
    )
    if item is None or not include_review_comments:
        return item
    return replace(
        item,
        review_comments=fetch_pr_review_comments(repo_path, number, runner=runner, use_cache=use_cache),
    )


def cached_pr(repo_path: Path, number: int) -> GitHubItem | None:
    """Return one PR from the local GitHub cache without invoking ``gh``."""
    return _cached_item(repo_path, kind="pr", number=number)


def fetch_pr_review_comments(
    repo_path: Path,
    number: int,
    *,
    runner: CommandRunner | None = None,
    use_cache: bool = True,
) -> tuple[GitHubReviewComment, ...]:
    """Fetch inline PR review comments for a PR.

    `gh pr view --json comments,reviews` covers top-level comments and review
    summaries. Inline review comments live on the pulls/comments REST endpoint,
    so they need an explicit drill-down fetch for PRs where review discussion
    matters.
    """
    if shutil.which("gh") is None and runner is None:
        return ()
    slug = repo_slug(repo_path) if runner is None else repo_slug_with_runner(repo_path, runner)
    if slug is None:
        return ()
    result = _run_cached(
        ["gh", "api", f"repos/{slug}/pulls/{number}/comments", "-f", "per_page=100"],
        repo_path,
        runner=runner,
        use_cache=use_cache,
    )
    if result.returncode != 0:
        return ()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(_review_comment(row) for row in payload if isinstance(row, dict))


def classify_lifecycle(item: GitHubItem) -> GitHubLifecycleClassification:
    """Classify issue/PR lifecycle semantics from state, labels, body, and comments."""
    text = _evidence_text(item)
    labels = {label.name.lower() for label in item.labels}
    reasons: list[str] = []

    if item.kind == "pr":
        if item.merged_at is not None or item.state == "merged":
            return GitHubLifecycleClassification("pr_closed", 0.95, ("merged PR",))
        if item.state == "open":
            return GitHubLifecycleClassification("open_frontier", 0.75, ("open PR",))
        return GitHubLifecycleClassification("unclear", 0.45, ("closed PR without merge evidence",))

    if labels & {"tracking", "epic", "roadmap", "umbrella"} or _TRACKING_RE.search(text):
        reasons.append("tracking/horizon wording or label")
        if item.state == "open":
            return GitHubLifecycleClassification("tracking_or_horizon", 0.8, tuple(reasons))

    if _CONSOLIDATED_RE.search(text):
        reasons.append("folded/consolidated wording")
        return GitHubLifecycleClassification("folded_or_consolidated", 0.78, tuple(reasons))

    if _MISFRAMED_RE.search(text):
        reasons.append("misframed/reframed wording")
        return GitHubLifecycleClassification("misframed", 0.72, tuple(reasons))

    if _RETIRED_RE.search(text):
        reasons.append("retired/stale wording")
        return GitHubLifecycleClassification("retired_stale", 0.76, tuple(reasons))

    if item.state == "open":
        return GitHubLifecycleClassification("open_frontier", 0.62, ("open issue",))

    if item.state == "closed":
        if _EXECUTED_RE.search(text):
            reasons.append("execution/merge wording")
            return GitHubLifecycleClassification("executed", 0.62, tuple(reasons))
        return GitHubLifecycleClassification("unclear", 0.46, ("closed issue without clear lifecycle wording",))

    return GitHubLifecycleClassification("unclear", 0.35, ("unknown state",))


def extract_issue_refs(text: str) -> tuple[int, ...]:
    refs = {int(match) for match in _ISSUE_REF_RE.findall(text or "")}
    refs.update(int(match) for match in _PR_SUFFIX_RE.findall(text or ""))
    return tuple(sorted(refs))


def extract_commit_refs(text: str) -> dict[str, set[int]]:
    """Extract PR suffix refs and explicit issue refs from commit text."""
    text = text or ""
    prs = {int(match) for match in _PR_SUFFIX_RE.findall(text)}
    issues = {int(match) for match in _ISSUE_REF_RE.findall(text)}
    return {"prs": prs, "issues": issues}


def lifecycle_summary(items: Iterable[GitHubItem]) -> dict[GitHubLifecycle, int]:
    summary: dict[GitHubLifecycle, int] = {}
    for item in items:
        lifecycle = classify_lifecycle(item).lifecycle
        summary[lifecycle] = summary.get(lifecycle, 0) + 1
    return summary


def _fetch_items(
    repo_path: Path,
    *,
    kind: GitHubItemKind,
    state: str,
    limit: int,
    runner: CommandRunner | None,
    use_cache: bool,
) -> GitHubFetchResult:
    if shutil.which("gh") is None and runner is None:
        return GitHubFetchResult("unavailable", repo_path.name, None, (), "gh_not_found")
    slug = repo_slug(repo_path) if runner is None else repo_slug_with_runner(repo_path, runner)
    if slug is None:
        return GitHubFetchResult("unavailable", repo_path.name, None, (), "github_remote_not_found")
    fields = _PR_FIELDS if kind == "pr" else _BODY_FIELDS
    args = ["gh", kind, "list", "--repo", slug, "--state", state, "--limit", str(limit), "--json", fields]
    result = _run_cached(args, repo_path, runner=runner, use_cache=use_cache)
    if result.returncode != 0:
        reason = result.stderr.strip() or f"gh_{kind}_list_failed"
        return GitHubFetchResult("error", repo_path.name, slug, (), reason)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return GitHubFetchResult("error", repo_path.name, slug, (), f"invalid_json: {exc}")
    if not isinstance(payload, list):
        return GitHubFetchResult("error", repo_path.name, slug, (), "unexpected_json_shape")
    items = tuple(_item_from_payload(repo_path.name, slug, kind, row) for row in payload if isinstance(row, dict))
    return GitHubFetchResult("ok", repo_path.name, slug, items)


def _fetch_item(
    repo_path: Path,
    *,
    kind: GitHubItemKind,
    number: int,
    runner: CommandRunner | None,
    use_cache: bool,
    max_age_seconds: int,
) -> GitHubItem | None:
    if shutil.which("gh") is None and runner is None:
        return None
    slug = repo_slug(repo_path) if runner is None else repo_slug_with_runner(repo_path, runner)
    if slug is None:
        return None
    fields = _PR_FIELDS if kind == "pr" else _BODY_FIELDS
    result = _run_cached(
        ["gh", kind, "view", str(number), "--repo", slug, "--json", fields],
        repo_path,
        runner=runner,
        use_cache=use_cache,
        max_age_seconds=max_age_seconds,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _item_from_payload(repo_path.name, slug, kind, payload)


def _cached_item(
    repo_path: Path,
    *,
    kind: GitHubItemKind,
    number: int,
) -> GitHubItem | None:
    slug = repo_slug(repo_path)
    if slug is None:
        return None
    fields = _PR_FIELDS if kind == "pr" else _BODY_FIELDS
    args = ["gh", kind, "view", str(number), "--repo", slug, "--json", fields]
    payload_json = _cache_get(_cache_key(args), max_age_seconds=None)
    if payload_json is None:
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _item_from_payload(repo_path.name, slug, kind, payload)


def repo_slug_with_runner(repo_path: Path, runner: CommandRunner) -> str | None:
    if not (repo_path / ".git").exists():
        return None
    result = runner(["git", "remote", "get-url", "origin"], repo_path)
    if result.returncode != 0:
        return None
    return slug_from_remote(result.stdout.strip())


def _item_from_payload(repo: str, slug: str, kind: GitHubItemKind, payload: dict[str, object]) -> GitHubItem:
    merge_commit = payload.get("mergeCommit")
    merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    return GitHubItem(
        repo=repo,
        slug=slug,
        kind=kind,
        number=_int(payload.get("number")),
        title=str(payload.get("title") or ""),
        state=_state(payload, kind),
        url=_str_or_none(payload.get("url")),
        author=_actor(payload.get("author")),
        labels=_labels(payload.get("labels")),
        body=str(payload.get("body") or ""),
        comments=_comments(payload.get("comments")),
        created_at=_dt(payload.get("createdAt")),
        updated_at=_dt(payload.get("updatedAt")),
        closed_at=_dt(payload.get("closedAt")),
        merged_at=_dt(payload.get("mergedAt")),
        merge_commit=_str_or_none(merge_sha),
        review_decision=_str_or_none(payload.get("reviewDecision")),
        reviews=_reviews(payload.get("reviews")),
        latest_reviews=_reviews(payload.get("latestReviews")),
    )


def _state(payload: dict[str, object], kind: GitHubItemKind) -> GitHubItemState:
    if kind == "pr" and payload.get("mergedAt"):
        return "merged"
    state = str(payload.get("state") or "").lower()
    if state in {"open", "closed", "merged"}:
        return state  # type: ignore[return-value]
    return "unknown"


def _actor(payload: object) -> GitHubActor:
    if isinstance(payload, dict):
        return GitHubActor(_str_or_none(payload.get("login")))
    return GitHubActor(None)


def _labels(payload: object) -> tuple[GitHubLabel, ...]:
    if not isinstance(payload, list):
        return ()
    labels = []
    for item in payload:
        if isinstance(item, dict) and item.get("name"):
            labels.append(GitHubLabel(str(item["name"])))
    return tuple(labels)


def _comments(payload: object) -> tuple[GitHubComment, ...]:
    if not isinstance(payload, list):
        return ()
    comments = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        comments.append(
            GitHubComment(
                author=_actor(item.get("author")),
                body=str(item.get("body") or ""),
                created_at=_dt(item.get("createdAt")),
                url=_str_or_none(item.get("url")),
            )
        )
    return tuple(comments)


def _reviews(payload: object) -> tuple[GitHubReview, ...]:
    if not isinstance(payload, list):
        return ()
    reviews = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        reviews.append(
            GitHubReview(
                author=_actor(item.get("author")),
                state=str(item.get("state") or ""),
                body=str(item.get("body") or ""),
                submitted_at=_dt(item.get("submittedAt")),
                url=_str_or_none(item.get("url")),
            )
        )
    return tuple(reviews)


def _review_comment(payload: dict[str, object]) -> GitHubReviewComment:
    return GitHubReviewComment(
        author=_actor(payload.get("user") or payload.get("author")),
        body=str(payload.get("body") or ""),
        path=_str_or_none(payload.get("path")),
        line=_int_or_none(payload.get("line") or payload.get("original_line")),
        diff_hunk=_str_or_none(payload.get("diff_hunk")),
        created_at=_dt(payload.get("created_at") or payload.get("createdAt")),
        url=_str_or_none(payload.get("html_url") or payload.get("url")),
        review_id=_int_or_none(payload.get("pull_request_review_id")),
    )


def _evidence_text(item: GitHubItem) -> str:
    labels = " ".join(label.name for label in item.labels)
    comments = "\n".join(comment.body for comment in item.comments)
    reviews = "\n".join(review.body for review in item.reviews)
    review_comments = "\n".join(comment.body for comment in item.review_comments)
    return "\n".join([item.title, labels, item.body, comments, reviews, review_comments])


def _dt(value: object) -> datetime | None:
    return parse_datetime(value) if isinstance(value, str) and value else None


def _int(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _run(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=30)


def _run_cached(
    args: Sequence[str],
    cwd: Path | None,
    *,
    runner: CommandRunner | None,
    use_cache: bool,
    max_age_seconds: int = GITHUB_CACHE_TTL_SECONDS,
) -> subprocess.CompletedProcess[str]:
    if runner is not None or not use_cache or not args or args[0] != "gh":
        return (runner or _run)(args, cwd)

    key = _cache_key(args)
    cache = _cache_get(key, max_age_seconds=max_age_seconds)
    if cache is not None:
        return subprocess.CompletedProcess(list(args), returncode=0, stdout=cache, stderr="")

    result = _run(args, cwd)
    if result.returncode == 0:
        _cache_put(key, result.stdout)
    return result


def _cache_path() -> Path:
    from ..core.config import get_config

    path = get_config().cache_dir / "github_frontier.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(args: Sequence[str]) -> str:
    return json.dumps(list(args), sort_keys=True)


def _cache_get(key: str, *, max_age_seconds: int | None) -> str | None:
    try:
        with contextlib.closing(sqlite3.connect(str(_cache_path()))) as conn:
            with conn:
                _ensure_cache(conn)
                row = conn.execute("SELECT fetched_at, payload FROM github_cache WHERE cache_key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    fetched_at, payload = row
    if max_age_seconds is not None and time.time() - float(fetched_at) > max_age_seconds:
        return None
    return str(payload)


def _cache_put(key: str, payload: str) -> None:
    try:
        with contextlib.closing(sqlite3.connect(str(_cache_path()))) as conn:
            with conn:
                _ensure_cache(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO github_cache(cache_key, fetched_at, payload) VALUES (?, ?, ?)",
                    (key, time.time(), payload),
                )
    except sqlite3.Error:
        return


def _ensure_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS github_cache (
            cache_key TEXT PRIMARY KEY,
            fetched_at REAL NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )


__all__ = [
    "GitHubActor",
    "GitHubComment",
    "GitHubFetchResult",
    "GitHubItem",
    "GitHubLabel",
    "GitHubLifecycleClassification",
    "GitHubReview",
    "GitHubReviewComment",
    "GITHUB_CACHE_TTL_SECONDS",
    "classify_lifecycle",
    "cached_issue",
    "cached_pr",
    "extract_commit_refs",
    "extract_issue_refs",
    "fetch_issue",
    "fetch_issues",
    "fetch_pr",
    "fetch_pr_review_comments",
    "fetch_prs",
    "lifecycle_summary",
    "repo_slug",
    "slug_from_remote",
]
