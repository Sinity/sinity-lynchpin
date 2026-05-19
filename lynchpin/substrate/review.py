"""PR review table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse ISO-8601 string to a UTC-aware datetime, returning None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def load_pr_review_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    projects: tuple[str, ...] | None = None,
    states: tuple[str, ...] | None = None,
    only_with_friction: bool = False,
    refresh_id: str | None = None,
) -> list[Any]:  # list[PrReviewRow]
    """SELECT and hydrate ``pr_review_row`` rows to ``PrReviewRow`` instances.

    ``created_at``, ``closed_at``, and ``merged_at`` are ``TIMESTAMPTZ`` in
    the substrate but ``str | None`` on ``PrReviewRow``; we call ``.isoformat()``
    on non-None DuckDB datetime values.

    ``review_decisions``, ``reviewers``, and ``friction_signals`` (``VARCHAR[]``)
    are converted from list to tuple.
    """
    from lynchpin.core.pr_review import PrReviewRow

    clauses: list[str] = []
    params: list[Any] = []

    add_in_filter("project", projects, clauses, params)
    add_in_filter("state", states, clauses, params)

    if only_with_friction:
        clauses.append("len(friction_signals) > 0")

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            project, number, title, state, url, author,
            created_at, closed_at, merged_at,
            review_count, review_decisions, review_round_count,
            reviewer_count, reviewers, review_comment_count,
            top_level_comment_count, changes_requested_count,
            approval_count, dismissed_count,
            time_to_first_review_minutes, time_to_close_minutes,
            time_to_merge_minutes, final_decision, friction_signals
        FROM pr_review_row
        {where}
        ORDER BY project, number
    """
    rows = conn.execute(sql, params).fetchall()

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    results: list[Any] = []
    for (
        project, number, title, state, url, author,
        created_at, closed_at, merged_at,
        review_count, review_decisions, review_round_count,
        reviewer_count, reviewers, review_comment_count,
        top_level_comment_count, changes_requested_count,
        approval_count, dismissed_count,
        time_to_first_review_minutes, time_to_close_minutes,
        time_to_merge_minutes, final_decision, friction_signals,
    ) in rows:
        results.append(
            PrReviewRow(
                project=project,
                number=number,
                title=title or "",
                state=state or "",
                url=url,
                author=author,
                created_at=_iso(created_at),
                closed_at=_iso(closed_at),
                merged_at=_iso(merged_at),
                review_count=review_count,
                review_decisions=tuple(review_decisions) if review_decisions else (),
                review_round_count=review_round_count,
                reviewer_count=reviewer_count,
                reviewers=tuple(reviewers) if reviewers else (),
                review_comment_count=review_comment_count,
                top_level_comment_count=top_level_comment_count,
                changes_requested_count=changes_requested_count,
                approval_count=approval_count,
                dismissed_count=dismissed_count,
                time_to_first_review_minutes=time_to_first_review_minutes,
                time_to_close_minutes=time_to_close_minutes,
                time_to_merge_minutes=time_to_merge_minutes,
                final_decision=final_decision or "",
                friction_signals=tuple(friction_signals) if friction_signals else (),
            )
        )
    return results


_PR_REVIEW_COLUMNS = (
    "project", "number", "title", "state", "url", "author",
    "created_at", "closed_at", "merged_at",
    "review_count", "review_decisions",
    "review_round_count", "reviewer_count", "reviewers",
    "review_comment_count", "top_level_comment_count",
    "changes_requested_count", "approval_count", "dismissed_count",
    "time_to_first_review_minutes", "time_to_close_minutes",
    "time_to_merge_minutes", "final_decision", "friction_signals",
)


def _pr_review_extract(r: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("number") or 0),
        r.get("title"),
        r.get("state"),
        r.get("url"),
        r.get("author"),
        _parse_iso(r.get("created_at")),
        _parse_iso(r.get("closed_at")),
        _parse_iso(r.get("merged_at")),
        int(r.get("review_count") or 0),
        list(r.get("review_decisions") or []),
        int(r.get("review_round_count") or 0),
        int(r.get("reviewer_count") or 0),
        list(r.get("reviewers") or []),
        int(r.get("review_comment_count") or 0),
        int(r.get("top_level_comment_count") or 0),
        int(r.get("changes_requested_count") or 0),
        int(r.get("approval_count") or 0),
        int(r.get("dismissed_count") or 0),
        r.get("time_to_first_review_minutes"),
        r.get("time_to_close_minutes"),
        r.get("time_to_merge_minutes"),
        r.get("final_decision"),
        list(r.get("friction_signals") or []),
    )


def promote_pr_review_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """INSERT pr_review_row rows from build_active_pr_review_topology prs[].

    ISO-8601 timestamp strings are parsed to timezone-aware datetimes.
    Missing keys default gracefully.
    """
    return promote_rows(
        conn,
        table="pr_review_row",
        columns=_PR_REVIEW_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=_pr_review_extract,
    )


__all__ = ["load_pr_review_rows", "promote_pr_review_rows"]
