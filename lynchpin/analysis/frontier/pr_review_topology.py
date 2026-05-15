"""PR review thread topology (M.7).

Models how a pull request actually progressed through review — not just
"did it land," but: how many rounds, how long until first review, who
reviewed, how many comments left vs. left-and-superseded, what review
decisions accumulated. Surfaces friction patterns that commit/PR
counts can't see.

Per-PR rows + per-project SLO rollup. Intended consumers: the
current-state pack (when review topology surfaces a project's friction)
and ad-hoc retrospective analysis ("which PRs took 5+ rounds last
month?").

Output: ``active_pr_review_topology.json``.

Caveats:

  - "Resolved" vs "unresolved" review comments require GitHub's GraphQL
    ``isResolved`` flag, which the current source layer doesn't pull.
    This module reports comment counts and round counts; the reader
    judges resolution from those signals.
  - Review-round count is approximated as the count of distinct review
    submissions per reviewer. A reviewer who submitted CHANGES_REQUESTED
    then later APPROVED counts as two rounds. This matches the
    operational meaning ("how many times did this go back-and-forth")
    even when it doesn't match GitHub's narrower "review round" UI
    counter.
  - SLO percentiles are computed only over the ``closed`` PR set within
    the window; in-flight PRs are excluded from time-to-merge but
    included in age-of-open-PR signals.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any, Iterable, Sequence

from ...core.pr_review import PrReviewRow
from ...sources.github import (
    GitHubItem,
    GitHubReview,
    fetch_pr,
    fetch_prs,
)
from ..core.io import resolve_analysis_path, save_json


@dataclass(frozen=True)
class ProjectReviewSLO:
    project: str
    pr_count: int
    closed_pr_count: int
    merged_pr_count: int
    median_time_to_first_review_minutes: float | None
    p75_time_to_first_review_minutes: float | None
    median_review_round_count: float | None
    p75_review_round_count: float | None
    median_time_to_merge_minutes: float | None
    p75_time_to_merge_minutes: float | None
    high_friction_pr_count: int
    review_round_distribution: dict[int, int]


# Friction signal labels: a PR carrying any of these is worth a closer
# read. They are descriptive, not failures.
_FRICTION_LABELS: dict[str, str] = {
    "many_rounds":               "≥4 review submissions across reviewers",
    "long_to_first_review":      "first review ≥3 days after PR opened",
    "changes_requested_then_merged": "merged after CHANGES_REQUESTED at least once",
    "no_review_at_merge":        "merged without an APPROVED review",
    "self_merge":                "author merged without external approval",
    "stale_open":                "open ≥30 days with no review activity",
    "review_comment_storm":      "≥20 inline review comments accumulated",
}


def build_active_pr_review_topology(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    repo_paths: Sequence[Path] | None = None,
    items: Iterable[tuple[str, Iterable[GitHubItem]]] | None = None,
) -> dict[str, Any]:
    """Build the per-PR + per-project SLO payload.

    ``items`` accepts caller-supplied ``(project, GitHubItem iterable)``
    pairs for tests; when omitted, fetches via the local checkout.
    """
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    selected = set(projects or ())

    pr_rows: list[PrReviewRow] = []
    if items is not None:
        per_project_iter: Iterable[tuple[str, Iterable[GitHubItem]]] = items
    else:
        per_project_iter = _resolve_repo_iter(
            snapshot_file=snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
            repo_paths=repo_paths,
            selected=selected,
            start=start,
        )

    for project, project_items in per_project_iter:
        if selected and project not in selected:
            continue
        for item in project_items:
            if item.kind != "pr":
                continue
            pr_rows.append(_summarize_pr(project=project, item=item, reference=end))

    slos = _build_project_slos(pr_rows, reference=end)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "summary": {
            "pr_count": len(pr_rows),
            "high_friction_pr_count": sum(1 for r in pr_rows if r.friction_signals),
            "total_review_rounds": sum(r.review_round_count for r in pr_rows),
        },
        "prs": [_pr_row_to_dict(row) for row in pr_rows],
        "projects": [_slo_to_dict(slo) for slo in slos],
        "friction_legend": _FRICTION_LABELS,
        "caveats": [
            "review_round_count approximates GitHub's review history by counting distinct review submissions per reviewer",
            "review-comment 'resolution' state is not available without GraphQL; this module reports counts only",
            "time_to_first_review_minutes is the gap between PR creation and first review submission; a self-author 'COMMENTED' review counts as a review",
            "SLO percentiles are computed only over closed PRs in the window",
        ],
    }


def run_active_pr_review_topology(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_pr_review_topology(
        start=start, end=end, projects=projects,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


# ── helpers ───────────────────────────────────────────────────────────────────


def _summarize_pr(*, project: str, item: GitHubItem, reference: date) -> PrReviewRow:
    reviews = sorted(
        item.reviews,
        key=lambda r: r.submitted_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    decisions = tuple(r.state for r in reviews if r.state)
    rounds = _review_round_count(reviews)
    reviewers = sorted({r.author.login for r in reviews if r.author and r.author.login})

    changes = sum(1 for r in reviews if r.state == "CHANGES_REQUESTED")
    approvals = sum(1 for r in reviews if r.state == "APPROVED")
    dismissed = sum(1 for r in reviews if r.state == "DISMISSED")

    first_review_at = next(
        (r.submitted_at for r in reviews if r.submitted_at is not None),
        None,
    )
    time_to_first_review = _delta_minutes(item.created_at, first_review_at)
    time_to_close = _delta_minutes(item.created_at, item.closed_at)
    time_to_merge = _delta_minutes(item.created_at, item.merged_at)

    final_decision = _final_decision(item, decisions)
    friction = _friction_signals(
        item=item,
        rounds=rounds,
        reviews=reviews,
        approvals=approvals,
        changes=changes,
        time_to_first_review=time_to_first_review,
        reference=reference,
    )

    return PrReviewRow(
        project=project,
        number=item.number,
        title=item.title,
        state=item.state,
        url=item.url,
        author=item.author.login if item.author else None,
        created_at=item.created_at.isoformat() if item.created_at else None,
        closed_at=item.closed_at.isoformat() if item.closed_at else None,
        merged_at=item.merged_at.isoformat() if item.merged_at else None,
        review_count=len(reviews),
        review_decisions=decisions,
        review_round_count=rounds,
        reviewer_count=len(reviewers),
        reviewers=tuple(reviewers),
        review_comment_count=len(item.review_comments),
        top_level_comment_count=len(item.comments),
        changes_requested_count=changes,
        approval_count=approvals,
        dismissed_count=dismissed,
        time_to_first_review_minutes=time_to_first_review,
        time_to_close_minutes=time_to_close,
        time_to_merge_minutes=time_to_merge,
        final_decision=final_decision,
        friction_signals=friction,
    )


def _review_round_count(reviews: Sequence[GitHubReview]) -> int:
    """A 'round' = one review submission per reviewer.

    Two reviews from the same reviewer at different times are two rounds.
    `COMMENTED` reviews count too — they're the "I looked but didn't decide
    yet" signal, which is part of the round structure.
    """
    return sum(1 for r in reviews if r.submitted_at is not None)


def _final_decision(item: GitHubItem, decisions: tuple[str, ...]) -> str:
    if item.merged_at is not None:
        return "merged"
    if item.state == "closed":
        # Closed without merge — was the last review CHANGES_REQUESTED?
        approved = "APPROVED" in decisions
        return "closed_unmerged_after_approval" if approved else "closed_unmerged"
    if item.review_decision:
        return item.review_decision.lower()
    if decisions:
        return f"open_after_{decisions[-1].lower()}"
    return "open_no_review"


def _friction_signals(
    *,
    item: GitHubItem,
    rounds: int,
    reviews: Sequence[GitHubReview],
    approvals: int,
    changes: int,
    time_to_first_review: float | None,
    reference: date,
) -> tuple[str, ...]:
    signals: list[str] = []
    if rounds >= 4:
        signals.append("many_rounds")
    if time_to_first_review is not None and time_to_first_review >= 3 * 24 * 60:
        signals.append("long_to_first_review")
    if item.merged_at is not None and changes >= 1:
        signals.append("changes_requested_then_merged")
    if item.merged_at is not None and approvals == 0:
        signals.append("no_review_at_merge")
    if (
        item.merged_at is not None
        and item.author is not None
        and approvals == 0
        and rounds == 0
    ):
        signals.append("self_merge")
    if item.state == "open" and item.created_at is not None:
        age_days = (reference - item.created_at.date()).days
        if age_days >= 30 and rounds == 0:
            signals.append("stale_open")
    if len(item.review_comments) >= 20:
        signals.append("review_comment_storm")
    return tuple(signals)


def _build_project_slos(
    rows: Sequence[PrReviewRow], *, reference: date,
) -> list[ProjectReviewSLO]:
    by_project: dict[str, list[PrReviewRow]] = defaultdict(list)
    for row in rows:
        by_project[row.project].append(row)

    slos: list[ProjectReviewSLO] = []
    for project in sorted(by_project):
        prs = by_project[project]
        closed = [r for r in prs if r.state == "closed" or r.merged_at is not None]
        merged = [r for r in prs if r.merged_at is not None]

        first_review_minutes = [
            r.time_to_first_review_minutes
            for r in closed
            if r.time_to_first_review_minutes is not None
        ]
        rounds = [r.review_round_count for r in closed]
        merge_minutes = [
            r.time_to_merge_minutes
            for r in merged
            if r.time_to_merge_minutes is not None
        ]

        round_distribution: Counter[int] = Counter(r.review_round_count for r in prs)

        slos.append(ProjectReviewSLO(
            project=project,
            pr_count=len(prs),
            closed_pr_count=len(closed),
            merged_pr_count=len(merged),
            median_time_to_first_review_minutes=(
                statistics.median(first_review_minutes) if first_review_minutes else None
            ),
            p75_time_to_first_review_minutes=_percentile(first_review_minutes, 0.75),
            median_review_round_count=(
                statistics.median(rounds) if rounds else None
            ),
            p75_review_round_count=_percentile(rounds, 0.75),
            median_time_to_merge_minutes=(
                statistics.median(merge_minutes) if merge_minutes else None
            ),
            p75_time_to_merge_minutes=_percentile(merge_minutes, 0.75),
            high_friction_pr_count=sum(1 for r in prs if r.friction_signals),
            review_round_distribution=dict(sorted(round_distribution.items())),
        ))
    return slos


def _resolve_repo_iter(
    *,
    snapshot_file: str | PathLike[str],
    repo_paths: Sequence[Path] | None,
    selected: set[str],
    start: date,
) -> Iterable[tuple[str, Iterable[GitHubItem]]]:
    """Resolve project → PR-iterable from active project snapshot or paths."""
    from ..core.io import load_json_if_exists

    if repo_paths is not None:
        for path in repo_paths:
            project = path.name
            if selected and project not in selected:
                continue
            yield project, _fetch_pr_items(path, start=start)
        return

    payload = load_json_if_exists(snapshot_file) or {}
    projects = payload.get("projects") if isinstance(payload, dict) else []
    for row in projects or ():
        if not isinstance(row, dict):
            continue
        row_project = row.get("project")
        path_str = row.get("path")
        if not isinstance(row_project, str) or not isinstance(path_str, str):
            continue
        repo_path = Path(path_str)
        if not repo_path.is_dir():
            continue
        if selected and row_project not in selected:
            continue
        yield row_project, _fetch_pr_items(repo_path, start=start)


def _fetch_pr_items(repo_path: Path, *, start: date) -> Iterable[GitHubItem]:
    """Fetch open and recent-closed PRs with reviews loaded.

    Two passes: ``open`` to capture in-flight, ``closed`` for those closed
    within the window. Then enrich each with ``include_review_comments``.
    """
    items: list[GitHubItem] = []
    for state in ("open", "closed"):
        result = fetch_prs(repo_path, state=state, limit=80, use_cache=True)  # type: ignore[arg-type]
        if result.status != "ok":
            continue
        for item in result.items:
            cutoff = item.closed_at or item.updated_at
            if state == "closed" and cutoff is not None and cutoff.date() < start:
                continue
            enriched = fetch_pr(repo_path, item.number, include_review_comments=True, use_cache=True)
            items.append(enriched if enriched is not None else item)
    return items


def _delta_minutes(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60.0, 1)


def _percentile(values: Sequence[float | int], pct: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    idx = max(0, min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1)))))
    return float(sorted_values[idx])


def _pr_row_to_dict(row: PrReviewRow) -> dict[str, Any]:
    return {
        "project": row.project,
        "number": row.number,
        "title": row.title,
        "state": row.state,
        "url": row.url,
        "author": row.author,
        "created_at": row.created_at,
        "closed_at": row.closed_at,
        "merged_at": row.merged_at,
        "review_count": row.review_count,
        "review_decisions": list(row.review_decisions),
        "review_round_count": row.review_round_count,
        "reviewer_count": row.reviewer_count,
        "reviewers": list(row.reviewers),
        "review_comment_count": row.review_comment_count,
        "top_level_comment_count": row.top_level_comment_count,
        "changes_requested_count": row.changes_requested_count,
        "approval_count": row.approval_count,
        "dismissed_count": row.dismissed_count,
        "time_to_first_review_minutes": row.time_to_first_review_minutes,
        "time_to_close_minutes": row.time_to_close_minutes,
        "time_to_merge_minutes": row.time_to_merge_minutes,
        "final_decision": row.final_decision,
        "friction_signals": list(row.friction_signals),
    }


def _slo_to_dict(slo: ProjectReviewSLO) -> dict[str, Any]:
    return {
        "project": slo.project,
        "pr_count": slo.pr_count,
        "closed_pr_count": slo.closed_pr_count,
        "merged_pr_count": slo.merged_pr_count,
        "median_time_to_first_review_minutes": slo.median_time_to_first_review_minutes,
        "p75_time_to_first_review_minutes": slo.p75_time_to_first_review_minutes,
        "median_review_round_count": slo.median_review_round_count,
        "p75_review_round_count": slo.p75_review_round_count,
        "median_time_to_merge_minutes": slo.median_time_to_merge_minutes,
        "p75_time_to_merge_minutes": slo.p75_time_to_merge_minutes,
        "high_friction_pr_count": slo.high_friction_pr_count,
        "review_round_distribution": slo.review_round_distribution,
    }


__all__ = [
    "ProjectReviewSLO",
    "build_active_pr_review_topology",
    "run_active_pr_review_topology",
]
