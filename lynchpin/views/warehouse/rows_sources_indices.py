from __future__ import annotations

from typing import Iterator, Tuple

from ...sources.indices import gitstats, session_summaries as session_summaries_source, sessions
from .core import WarehouseContext, _json_dumps, _maybe_limit


def _gitstats_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for commit in _maybe_limit(gitstats.iter_commits(), ctx.limit):
        yield (
            commit.date,
            commit.repo,
            commit.commit,
            commit.lines_added,
            commit.lines_deleted,
            commit.subject,
        )


def _sessions_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(sessions.iter_sessions(), ctx.limit):
        yield (
            record.date,
            record.provider,
            record.label,
            record.doc_path,
            record.highlights,
        )


def _session_summaries_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for summary in _maybe_limit(session_summaries_source.iter_session_summaries(), ctx.limit):
        yield (
            str(summary.summary_path),
            summary.source_path,
            summary.provider,
            summary.title,
            summary.timeframe,
            summary.summary,
            summary.generated_at,
            len(summary.highlights),
            len(summary.decisions),
            len(summary.follow_ups),
            len(summary.action_items),
            len(summary.risks),
            _json_dumps(summary.highlights),
            _json_dumps(summary.decisions),
            _json_dumps(summary.follow_ups),
            _json_dumps(summary.action_items),
            _json_dumps(summary.risks),
            _json_dumps(summary.raw_references),
        )
