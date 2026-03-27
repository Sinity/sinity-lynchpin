from __future__ import annotations

from typing import Iterator, Tuple

from ...sources.libraries import dendron, finance, substack
from .core import WarehouseContext, _json_dumps, _maybe_limit


def _dendron_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for note in _maybe_limit(dendron.iter_notes(), ctx.limit):
        yield (
            str(note.path),
            note.id,
            note.title,
            _json_dumps(note.tags),
            _json_dumps(note.frontmatter),
            note.body,
        )


def _finance_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for row in _maybe_limit(finance.iter_transactions(), ctx.limit):
        for idx, posting in enumerate(row.postings):
            yield (
                row.date,
                row.payee,
                row.narration,
                idx,
                posting.account,
                posting.amount,
                posting.currency or "",
                None,
            )


def _substack_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for post in _maybe_limit(substack.iter_posts(), ctx.limit):
        yield (
            post.source,
            str(post.path),
            post.published_at,
            post.slug,
            post.title,
            post.format,
            post.content,
        )
