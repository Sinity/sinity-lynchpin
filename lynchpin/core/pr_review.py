"""Shared PR review topology row contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrReviewRow:
    """Per-PR review-thread snapshot."""

    project: str
    number: int
    title: str
    state: str
    url: str | None
    author: str | None
    created_at: str | None
    closed_at: str | None
    merged_at: str | None
    review_count: int
    review_decisions: tuple[str, ...]
    review_round_count: int
    reviewer_count: int
    reviewers: tuple[str, ...]
    review_comment_count: int
    top_level_comment_count: int
    changes_requested_count: int
    approval_count: int
    dismissed_count: int
    time_to_first_review_minutes: float | None
    time_to_close_minutes: float | None
    time_to_merge_minutes: float | None
    final_decision: str
    friction_signals: tuple[str, ...]


__all__ = ["PrReviewRow"]
