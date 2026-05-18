"""Refresh-DAG step: promote source data + evidence graph to DuckDB substrate.

Writes to the substrate as a side effect of refresh; does not change any
existing read path. Read-side cutover comes in Arc 3.

Arc 2.6 cutover: substrate becomes populated by default on every refresh run,
ready for Arc 4 (MCP server) to read from.

Per-source readiness (Arc 2.7): every source's outcome is recorded in
``substrate_source_status`` (status: ok | empty | unavailable | error). This
fixes the prior silent-failure mode where a stale polylogue archive →
``ai_work_event=0`` looked indistinguishable from a successful promote with
no events in the window.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import date, datetime, timezone

from .substrate_promote_ai import promote_ai_sources
from .substrate_promote_artifacts import promote_artifact_sources
from .substrate_promote_graph import promote_graph_source
from .substrate_promote_machine import promote_machine_tables
from .substrate_promote_personal import promote_personal_sources
from .substrate_promote_review import promote_review_source
from .substrate_promote_status import (
    MACHINE_SOURCE_IDS,
    SOURCE_AI_WORK_EVENTS,
    SOURCE_CALENDAR,
    SOURCE_COMMITS,
    SOURCE_EVIDENCE_GRAPH,
    SOURCE_FILE_CHANGES,
    SOURCE_MACHINE,
    SOURCE_MACHINE_EXPERIMENTS,
    SOURCE_MACHINE_GPU,
    SOURCE_MACHINE_NETWORK,
    SOURCE_MACHINE_SERVICE_STATE,
    SOURCE_SINNIX_GENERATION,
    SOURCE_BORG_DRILL,
    SOURCE_PR_REVIEW,
    SOURCE_SPOTIFY_DAILY,
    SOURCE_SYMBOLS,
    SourceSelection,
)

log = logging.getLogger(__name__)


def run_substrate_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None = None,
    ai_attribution_file: str | None = None,
    sources: Collection[str] | None = None,
    refresh_id: str | None = None,
    write_evidence_graph: bool = True,
) -> dict[str, int]:
    """Promote refresh outputs and live source families to the substrate.

    JSON artifacts, AI work events, evidence graph, PR review rows, personal
    exports, and machine telemetry each preserve their own source-status row.

    Returns per-table row counts.
    """
    selection = SourceSelection.from_collection(sources)
    try:
        return _do_promote(
            commit_facts_file=commit_facts_file,
            file_changes_file=file_changes_file,
            symbol_changes_file=symbol_changes_file,
            pr_review_file=pr_review_file,
            ai_attribution_file=ai_attribution_file,
            refresh_id=refresh_id,
            selection=selection,
            write_evidence_graph=write_evidence_graph,
        )
    except Exception as exc:  # noqa: BLE001 — refresh promotion must be best-effort
        log.warning("substrate_promote: substrate promotion failed: %s", exc)
        return {}


def _do_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None,
    ai_attribution_file: str | None,
    refresh_id: str | None,
    selection: SourceSelection,
    write_evidence_graph: bool,
) -> dict[str, int]:
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    refresh_id = refresh_id or f"dag:{datetime.now(timezone.utc).isoformat()}"
    counts: dict[str, int] = {}

    # Hard-coded window: current month + previous month (covers ~30-60 days of
    # recent AI activity).  Refine via spec config in a follow-up.
    today = date.today()
    if today.month == 1:
        prev_month_start = today.replace(year=today.year - 1, month=12, day=1)
    else:
        prev_month_start = today.replace(month=today.month - 1, day=1)
    window_start = prev_month_start
    window_end = today

    with connect(substrate_path()) as conn:
        apply_schema(conn)

        promote_artifact_sources(
            conn,
            refresh_id=refresh_id,
            commit_facts_file=commit_facts_file,
            file_changes_file=file_changes_file,
            symbol_changes_file=symbol_changes_file,
            ai_attribution_file=ai_attribution_file,
            counts=counts,
            selection=selection,
        )

        promote_ai_sources(
            conn,
            refresh_id=refresh_id,
            window_start=window_start,
            window_end=window_end,
            counts=counts,
            selection=selection,
        )

        promote_graph_source(
            conn,
            refresh_id=refresh_id,
            window_start=window_start,
            window_end=window_end,
            counts=counts,
            selection=selection,
            write_evidence_graph=write_evidence_graph,
        )

        promote_review_source(
            conn,
            refresh_id=refresh_id,
            pr_review_file=pr_review_file,
            counts=counts,
            selection=selection,
        )

        promote_personal_sources(
            conn,
            refresh_id=refresh_id,
            window_start=window_start,
            window_end=window_end,
            counts=counts,
            selection=selection,
        )

        if selection.includes(*MACHINE_SOURCE_IDS):
            promote_machine_tables(
                conn,
                refresh_id=refresh_id,
                window_start=window_start,
                window_end=window_end,
                counts=counts,
                selection=selection,
            )

    log.info(
        "substrate promotion complete: refresh_id=%s counts=%s",
        refresh_id,
        counts,
    )
    return counts


__all__ = [
    "run_substrate_promote",
    "SOURCE_COMMITS",
    "SOURCE_FILE_CHANGES",
    "SOURCE_SYMBOLS",
    "SOURCE_AI_WORK_EVENTS",
    "SOURCE_EVIDENCE_GRAPH",
    "SOURCE_PR_REVIEW",
    "SOURCE_CALENDAR",
    "SOURCE_SPOTIFY_DAILY",
    "SOURCE_MACHINE",
    "SOURCE_MACHINE_GPU",
    "SOURCE_MACHINE_NETWORK",
    "SOURCE_MACHINE_SERVICE_STATE",
    "SOURCE_MACHINE_EXPERIMENTS",
    "SOURCE_SINNIX_GENERATION",
    "SOURCE_BORG_DRILL",
]
