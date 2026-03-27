"""Processed git-plane row generators."""

from __future__ import annotations

from typing import Iterator, Tuple

from .core import WarehouseContext, _json_dumps, _maybe_limit
from .rows_processed_range import _bounded_date_range


def _processed_git_daily_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_activity import iter_git_daily

    start_d, end_d = _bounded_date_range(ctx)
    for row in _maybe_limit(iter_git_daily(start=start_d, end=end_d), ctx.limit):
        yield (
            row.date,
            row.repo,
            row.commit_count,
            row.lines_added,
            row.lines_deleted,
            row.churn,
            row.net_loc,
            row.ai_coauthored,
            row.ai_ratio,
            row.dominant_prefix,
            row.commit_burst_count,
        )


def _processed_git_commit_fact_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_commit_facts import iter_git_commit_facts

    start_d, end_d = _bounded_date_range(ctx)
    for fact in _maybe_limit(iter_git_commit_facts(start=start_d, end=end_d), ctx.limit):
        yield (
            fact.date,
            fact.repo,
            fact.authored_at,
            fact.commit,
            fact.author,
            fact.subject,
            fact.lines_added,
            fact.lines_deleted,
            fact.lines_changed,
            fact.files_changed,
            _json_dumps(fact.path_roots),
            _json_dumps(fact.paths),
        )


def _processed_git_file_fact_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_commit_facts import iter_git_file_change_facts

    start_d, end_d = _bounded_date_range(ctx)
    for fact in _maybe_limit(iter_git_file_change_facts(start=start_d, end=end_d), ctx.limit):
        yield (
            fact.date,
            fact.repo,
            fact.authored_at,
            fact.commit,
            fact.path,
            fact.path_root,
            fact.lines_added,
            fact.lines_deleted,
            fact.lines_changed,
        )


def _processed_commit_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    from ...sources.processed.git_activity import iter_commit_sessions

    start_d, end_d = _bounded_date_range(ctx)
    for session in _maybe_limit(iter_commit_sessions(start=start_d, end=end_d), ctx.limit):
        yield (
            session.repo,
            session.start,
            session.end,
            session.commits,
            session.is_burst,
            session.ai_fraction,
            session.lines_changed,
        )
