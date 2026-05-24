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
from dataclasses import dataclass
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
    SOURCE_PERSONAL_DAILY_SIGNAL,
    SOURCE_SPOTIFY_DAILY,
    SOURCE_SYMBOLS,
    SourceSelection,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromotionSourceStatus:
    source: str
    kind: str
    status: str
    reason: str | None
    row_count: int


@dataclass(frozen=True)
class PromotionRunResult:
    refresh_id: str
    status: str
    reason: str | None
    counts: dict[str, int]
    source_statuses: tuple[PromotionSourceStatus, ...]
    started_at: datetime
    finished_at: datetime

    def get(self, key: str, default: int | None = None) -> int | None:
        return self.counts.get(key, default)

    def __getitem__(self, key: str) -> int:
        return self.counts[key]

    def __contains__(self, key: object) -> bool:
        return key in self.counts


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
    window_start: date | None = None,
    window_end: date | None = None,
) -> PromotionRunResult:
    """Promote refresh outputs and live source families to the substrate.

    JSON artifacts, AI work events, evidence graph, PR review rows, personal
    exports, and machine telemetry each preserve their own source-status row.

    Returns per-table row counts.
    """
    selection = SourceSelection.from_collection(sources)
    started_at = datetime.now(timezone.utc)
    refresh_id = refresh_id or f"dag:{started_at.isoformat()}"
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
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:  # noqa: BLE001 — refresh promotion must be best-effort
        log.warning("substrate_promote: substrate promotion failed: %s", exc)
        finished_at = datetime.now(timezone.utc)
        result = PromotionRunResult(
            refresh_id=refresh_id,
            status="error",
            reason=str(exc),
            counts={},
            source_statuses=(),
            started_at=started_at,
            finished_at=finished_at,
        )
        _record_failed_promotion_run(result, window_start=window_start, window_end=window_end)
        return result


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
    window_start: date | None,
    window_end: date | None,
) -> PromotionRunResult:
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    started_at = datetime.now(timezone.utc)
    counts: dict[str, int] = {}

    if window_start is None or window_end is None:
        today = date.today()
        if today.month == 1:
            prev_month_start = today.replace(year=today.year - 1, month=12, day=1)
        else:
            prev_month_start = today.replace(month=today.month - 1, day=1)
        window_start = window_start or prev_month_start
        window_end = window_end or today

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

        source_statuses = _source_statuses(conn, refresh_id)
        status = _run_status(source_statuses)
        reason = _run_reason(source_statuses)
        finished_at = datetime.now(timezone.utc)
        _record_promotion_run(
            conn,
            result=PromotionRunResult(
                refresh_id=refresh_id or "",
                status=status,
                reason=reason,
                counts=dict(counts),
                source_statuses=source_statuses,
                started_at=started_at,
                finished_at=finished_at,
            ),
            window_start=window_start,
            window_end=window_end,
        )

    log.info(
        "substrate promotion complete: refresh_id=%s counts=%s",
        refresh_id,
        counts,
    )
    return PromotionRunResult(
        refresh_id=refresh_id or "",
        status=status,
        reason=reason,
        counts=dict(counts),
        source_statuses=source_statuses,
        started_at=started_at,
        finished_at=finished_at,
    )


def _source_statuses(conn: object, refresh_id: str) -> tuple[PromotionSourceStatus, ...]:
    rows = conn.execute(
        "SELECT source, kind, status, reason, row_count FROM substrate_source_status WHERE refresh_id = ? ORDER BY kind, source",
        [refresh_id],
    ).fetchall()
    return tuple(
        PromotionSourceStatus(
            source=row[0],
            kind=row[1],
            status=row[2],
            reason=row[3],
            row_count=int(row[4] or 0),
        )
        for row in rows
    )


def _run_status(statuses: tuple[PromotionSourceStatus, ...]) -> str:
    if any(row.status == "error" for row in statuses):
        return "error"
    if any(row.status == "unavailable" for row in statuses):
        return "degraded"
    return "ok"


def _run_reason(statuses: tuple[PromotionSourceStatus, ...]) -> str | None:
    bad = [row for row in statuses if row.status in {"error", "unavailable"}]
    if not bad:
        return None
    return "; ".join(f"{row.source}: {row.reason or row.status}" for row in bad[:6])


def _record_failed_promotion_run(
    result: PromotionRunResult,
    *,
    window_start: date | None,
    window_end: date | None,
) -> None:
    try:
        from lynchpin.substrate.connection import apply_schema, connect, substrate_path

        with connect(substrate_path()) as conn:
            apply_schema(conn)
            _record_promotion_run(
                conn,
                result=result,
                window_start=window_start,
                window_end=window_end,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("substrate_promote: failed to record failed promotion run: %s", exc)


def _record_promotion_run(
    conn: object,
    *,
    result: PromotionRunResult,
    window_start: date | None,
    window_end: date | None,
) -> None:
    import json

    conn.execute("DELETE FROM substrate_promotion_run WHERE refresh_id = ?", [result.refresh_id])
    conn.execute(
        """
        INSERT INTO substrate_promotion_run
        (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            result.refresh_id,
            result.status,
            result.reason,
            window_start,
            window_end,
            "materialized",
            json.dumps(result.counts, sort_keys=True),
            result.started_at,
            result.finished_at,
        ],
    )


__all__ = [
    "run_substrate_promote",
    "PromotionRunResult",
    "PromotionSourceStatus",
    "SOURCE_COMMITS",
    "SOURCE_FILE_CHANGES",
    "SOURCE_SYMBOLS",
    "SOURCE_AI_WORK_EVENTS",
    "SOURCE_EVIDENCE_GRAPH",
    "SOURCE_PR_REVIEW",
    "SOURCE_SPOTIFY_DAILY",
    "SOURCE_PERSONAL_DAILY_SIGNAL",
    "SOURCE_MACHINE",
    "SOURCE_MACHINE_GPU",
    "SOURCE_MACHINE_NETWORK",
    "SOURCE_MACHINE_SERVICE_STATE",
    "SOURCE_MACHINE_EXPERIMENTS",
    "SOURCE_SINNIX_GENERATION",
    "SOURCE_BORG_DRILL",
]
