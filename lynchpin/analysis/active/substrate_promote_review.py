"""PR review promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .substrate_promote_loaders import _load_pr_review_rows
from .substrate_promote_status import (
    SOURCE_PR_REVIEW,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_review_source(
    conn: Any,
    *,
    refresh_id: str,
    pr_review_file: str | None,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_PR_REVIEW):
        return

    from lynchpin.substrate.review import promote_pr_review_rows

    if pr_review_file is None:
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_PR_REVIEW,
            status="unavailable",
            reason="pr_review_file not provided to substrate_promote",
            row_count=0,
        )
        return

    try:
        pr_rows = list(_load_pr_review_rows(pr_review_file))
    except Exception as exc:
        log.warning("substrate_promote: pr_review hydration failed: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_PR_REVIEW,
            status="error",
            reason=str(exc),
            row_count=0,
        )
        pr_rows = []

    if pr_rows:
        try:
            counts["pr_review_rows"] = promote_pr_review_rows(
                conn,
                refresh_id=refresh_id,
                rows=pr_rows,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_PR_REVIEW,
                status="ok",
                reason=None,
                row_count=counts["pr_review_rows"],
            )
        except Exception as exc:
            log.warning("substrate_promote: pr_review promotion failed: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_PR_REVIEW,
                status="error",
                reason=str(exc),
                row_count=0,
            )
        return

    if not Path(pr_review_file).exists():
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_PR_REVIEW,
            status="unavailable",
            reason="active_pr_review_topology.json missing — run pr_review_topology",
            row_count=0,
        )
    else:
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_PR_REVIEW,
            status="empty",
            reason="no PR rows in payload",
            row_count=0,
        )
