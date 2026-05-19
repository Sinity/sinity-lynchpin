"""AI work-event table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

_TIER_RANK_SQL = "CASE kind_tier WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"

_TIER_RANK_VALUES: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def load_ai_work_events(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    kinds: tuple[str, ...] | None = None,
    min_kind_tier: Literal["high", "medium", "low"] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:  # list[WorkEvent]
    """SELECT and hydrate ``ai_work_event`` rows to ``WorkEvent`` instances.

    ``kind_tier`` and ``kind_source`` are substrate-only columns (not on
    ``WorkEvent``). They are used for filtering here and discarded on hydration.
    Use ``load_ai_work_event_labels`` if you need them.

    Date filtering: when ``start`` or ``end`` is given, events with
    ``start_ts IS NULL`` are **excluded** — they cannot be placed in time.
    Without a date filter, all events are returned regardless of ``start_ts``.

    ``file_paths`` and ``tools_used`` (``VARCHAR[]``) are converted from list
    to tuple.
    """
    from lynchpin.sources.polylogue import WorkEvent

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("start_ts", start, end, clauses, params, nullable=True)
    add_in_filter("project", projects, clauses, params)
    add_in_filter("kind", kinds, clauses, params)

    if min_kind_tier is not None:
        min_rank = _TIER_RANK_VALUES.get(min_kind_tier, 0)
        clauses.append(f"({_TIER_RANK_SQL}) >= ?")
        params.append(min_rank)

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            event_id, conversation_id, provider, kind, kind_confidence,
            start_ts, end_ts, duration_ms, file_paths, tools_used, summary
        FROM ai_work_event
        {where}
        ORDER BY start_ts NULLS LAST, event_id
    """
    rows = conn.execute(sql, params).fetchall()

    results: list[Any] = []
    for (
        event_id,
        conversation_id,
        provider,
        kind,
        kind_confidence,
        start_ts,
        end_ts,
        duration_ms,
        file_paths,
        tools_used,
        summary,
    ) in rows:
        results.append(
            WorkEvent(
                event_id=event_id,
                conversation_id=conversation_id,
                provider=provider,
                kind=kind,
                confidence=kind_confidence,
                start=start_ts,
                end=end_ts,
                duration_ms=duration_ms,
                file_paths=tuple(file_paths) if file_paths else (),
                tools_used=tuple(tools_used) if tools_used else (),
                summary=summary or "",
            )
        )
    return results


# ---------------------------------------------------------------------------
# ai_work_event — label view
# ---------------------------------------------------------------------------


def load_ai_work_event_labels(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
) -> dict[str, Any]:  # dict[str, WorkEventKindLabel]
    """Return ``event_id → WorkEventKindLabel`` mapping.

    Includes the substrate-only tier/source columns that ``load_ai_work_events``
    discards. Useful for callers that want to inspect or render classification
    metadata.
    """
    from lynchpin.core.work_event_kind import WorkEventKindLabel

    clauses: list[str] = []
    params: list[Any] = []

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            event_id, kind, kind_confidence, kind_source, kind_tier,
            polylogue_kind, polylogue_confidence,
            overlay_kind, overlay_confidence
        FROM ai_work_event
        {where}
    """
    rows = conn.execute(sql, params).fetchall()

    out: dict[str, Any] = {}
    for (
        event_id,
        kind,
        kind_confidence,
        kind_source,
        kind_tier,
        polylogue_kind,
        polylogue_confidence,
        overlay_kind,
        overlay_confidence,
    ) in rows:
        out[event_id] = WorkEventKindLabel(
            kind=kind,
            confidence=kind_confidence,
            source=kind_source or "polylogue",
            tier=kind_tier or "low",
            polylogue_kind=polylogue_kind,
            polylogue_confidence=polylogue_confidence or 0.0,
            overlay_kind=overlay_kind,
            overlay_confidence=overlay_confidence or 0.0,
            # ``features`` (raw extractor signals) are not stored in the
            # substrate — callers get an empty dict here.
            features={},
        )
    return out


# ---------------------------------------------------------------------------
# symbol_change
# ---------------------------------------------------------------------------


_AI_WORK_EVENT_COLUMNS = (
    "event_id", "conversation_id", "provider", "project",
    "kind", "kind_confidence", "kind_tier", "kind_source",
    "polylogue_kind", "polylogue_confidence",
    "overlay_kind", "overlay_confidence",
    "file_paths", "tools_used",
    "start_ts", "end_ts", "duration_ms",
    "summary",
)


def promote_ai_work_events(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    events: Iterable[Any],  # Iterable[WorkEvent]
    project_resolver: Callable[[Any], str | None] | None = None,
    classifier: Callable[[Any], Any] | None = None,  # (WorkEvent) -> WorkEventKindLabel
) -> int:
    """INSERT ai_work_event rows, idempotent on refresh_id.

    When ``classifier`` is None, polylogue's raw kind is stored in both
    ``kind`` and ``polylogue_kind``; overlay/tier columns are NULL.
    When provided, all kind/tier/source/confidence columns are derived from
    the returned ``WorkEventKindLabel``.
    """

    def extract(ev: Any) -> tuple[Any, ...]:
        proj = project_resolver(ev) if project_resolver else None

        if classifier is not None:
            label = classifier(ev)
            kind = label.kind
            kind_confidence = label.confidence
            kind_tier = label.tier
            kind_source = label.source
            polylogue_kind = label.polylogue_kind
            polylogue_confidence = label.polylogue_confidence
            overlay_kind = label.overlay_kind
            overlay_confidence = label.overlay_confidence
        else:
            kind = ev.kind
            kind_confidence = float(ev.confidence) if ev.confidence is not None else 0.0
            kind_tier = None
            kind_source = None
            polylogue_kind = ev.kind
            polylogue_confidence = (
                float(ev.confidence) if ev.confidence is not None else 0.0
            )
            overlay_kind = None
            overlay_confidence = None

        return (
            ev.event_id, ev.conversation_id, ev.provider, proj,
            kind, kind_confidence, kind_tier, kind_source,
            polylogue_kind, polylogue_confidence,
            overlay_kind, overlay_confidence,
            list(ev.file_paths), list(ev.tools_used),
            ev.start, ev.end, int(ev.duration_ms),
            ev.summary or None,
        )

    return promote_rows(
        conn,
        table="ai_work_event",
        columns=_AI_WORK_EVENT_COLUMNS,
        refresh_id=refresh_id,
        rows=events,
        extractor=extract,
    )


# ── symbol_change ─────────────────────────────────────────────────────────────

__all__ = ["load_ai_work_event_labels", "load_ai_work_events", "promote_ai_work_events"]
