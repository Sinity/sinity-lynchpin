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

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Source identifiers used in substrate_source_status.source — kept as constants
# so MCP/readiness consumers can match against a stable vocabulary.
SOURCE_COMMITS = "commits"
SOURCE_FILE_CHANGES = "file_changes"
SOURCE_SYMBOLS = "symbols"
SOURCE_AI_WORK_EVENTS = "ai_work_events"
SOURCE_EVIDENCE_GRAPH = "evidence_graph"
SOURCE_PR_REVIEW = "pr_review"
SOURCE_CALENDAR = "calendar"


def run_substrate_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None = None,
    ai_attribution_file: str | None = None,
    spec_path: str | None = None,
    refresh_id: str | None = None,
    write_evidence_graph: bool = True,
) -> dict[str, int]:
    """Promote produced JSON artifacts + work events to the substrate.

    Reads already-materialized JSON outputs from the DAG (commit facts, file
    changes, symbol changes) and live polylogue work_events for the current
    window, then promotes all to DuckDB.

    Returns per-table row counts.  Errors are logged and swallowed — the
    refresh DAG must remain green even if the substrate is unavailable.
    """
    try:
        return _do_promote(
            commit_facts_file=commit_facts_file,
            file_changes_file=file_changes_file,
            symbol_changes_file=symbol_changes_file,
            pr_review_file=pr_review_file,
            refresh_id=refresh_id,
            write_evidence_graph=write_evidence_graph,
        )
    except Exception as exc:
        log.warning("substrate promotion failed (refresh continues): %s", exc)
        return {}


def _record_status(
    conn: Any,
    *,
    refresh_id: str,
    source: str,
    status: str,
    reason: str | None,
    row_count: int,
    window_start: date | None = None,
    window_end: date | None = None,
) -> None:
    """Upsert a per-source status row into ``substrate_source_status``."""
    conn.execute(
        "DELETE FROM substrate_source_status WHERE refresh_id = ? AND source = ?",
        [refresh_id, source],
    )
    conn.execute(
        """
        INSERT INTO substrate_source_status
        (refresh_id, source, status, reason, row_count, window_start, window_end, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            source,
            status,
            reason,
            int(row_count),
            window_start,
            window_end,
            datetime.now(timezone.utc),
        ],
    )


def _do_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None,
    refresh_id: str | None,
    write_evidence_graph: bool,
) -> dict[str, int]:
    from lynchpin.duck.connection import apply_schema, connect, substrate_path
    from lynchpin.duck.promote import (
        promote_ai_work_events,
        promote_calendar_events,
        promote_commits,
        promote_evidence_graph,
        promote_file_changes,
        promote_pr_review_rows,
        promote_symbol_changes,
    )
    from lynchpin.composite.work_event_kind import overlay_label

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

        # ── commits: read JSON, hydrate to GitCommitFact, promote ────────────
        try:
            commit_facts = list(_load_commit_facts(commit_facts_file))
        except Exception as exc:
            log.warning("substrate_promote: commit facts hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                status="error", reason=str(exc), row_count=0,
            )
            commit_facts = []

        if commit_facts:
            try:
                counts["commits"] = promote_commits(
                    conn, refresh_id=refresh_id, facts=commit_facts,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="ok", reason=None, row_count=counts["commits"],
                )
            except Exception as exc:
                log.warning("substrate_promote: commit promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: no commit facts to promote")
            if not Path(commit_facts_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="unavailable", reason="active_commit_facts.json missing",
                    row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="empty", reason="no commits in active facts payload",
                    row_count=0,
                )

        # ── file_changes: same pattern ────────────────────────────────────────
        try:
            fc_facts = list(_load_file_change_facts(file_changes_file))
        except Exception as exc:
            log.warning("substrate_promote: file change hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                status="error", reason=str(exc), row_count=0,
            )
            fc_facts = []

        if fc_facts:
            try:
                counts["file_changes"] = promote_file_changes(
                    conn, refresh_id=refresh_id, facts=fc_facts,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="ok", reason=None, row_count=counts["file_changes"],
                )
            except Exception as exc:
                log.warning("substrate_promote: file change promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: no file change facts to promote")
            if not Path(file_changes_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="unavailable",
                    reason="active_file_change_facts.json missing", row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="empty", reason="no file changes in active facts payload",
                    row_count=0,
                )

        # ── symbol_changes: load events list, promote ─────────────────────────
        try:
            symbol_rows = list(_load_symbol_change_rows(symbol_changes_file))
        except Exception as exc:
            log.warning("substrate_promote: symbol change hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                status="error", reason=str(exc), row_count=0,
            )
            symbol_rows = []

        if symbol_rows:
            try:
                counts["symbols"] = promote_symbol_changes(
                    conn, refresh_id=refresh_id, rows=symbol_rows,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="ok", reason=None, row_count=counts["symbols"],
                )
            except Exception as exc:
                log.warning("substrate_promote: symbol promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: active_symbol_changes.json missing or empty — skipping")
            if not Path(symbol_changes_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="unavailable",
                    reason="active_symbol_changes.json missing", row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="empty", reason="no symbol events in payload",
                    row_count=0,
                )

        # ── ai_work_events: pull via polylogue source ─────────────────────────
        # Window covers current + previous month to ensure recent sessions are
        # captured even when the refresh runs near month boundaries.
        try:
            from lynchpin.sources.polylogue import work_events

            def _classify(ev: Any) -> Any:
                return overlay_label(
                    polylogue_kind=ev.kind,
                    polylogue_confidence=float(ev.confidence or 0.0),
                    file_paths=ev.file_paths,
                    tools_used=ev.tools_used,
                    duration_ms=int(ev.duration_ms or 0),
                )

            events = list(work_events(start=window_start, end=window_end))
            if events:
                counts["ai_work_events"] = promote_ai_work_events(
                    conn, refresh_id=refresh_id, events=events, classifier=_classify,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                    status="ok", reason=None, row_count=counts["ai_work_events"],
                    window_start=window_start, window_end=window_end,
                )
            else:
                # Distinguish "facade returned []" (could be stale-and-silent) from
                # "facade raised". sources.polylogue.work_events() catches the
                # ArchiveInsightUnavailableError, logs a warning, and returns [],
                # so an empty list here may mean either:
                #   - the polylogue archive is genuinely empty in the window, OR
                #   - the polylogue session_insight rows are stale (the actual
                #     bug we hit during 2026-05-08 I.1 prep).
                # We probe archive_readiness to disambiguate.
                from lynchpin.sources.polylogue import archive_readiness

                readiness = archive_readiness()
                if readiness.work_event_count == 0:
                    status = "empty"
                    reason = "polylogue archive has no work events in window"
                elif readiness.status != "ready":
                    status = "unavailable"
                    reason = (
                        f"polylogue not ready (status={readiness.status}): "
                        f"{readiness.reason}"
                    )
                else:
                    # Archive reports ready but facade returned []. Could be a
                    # window-mismatch or a stale-rows readiness flag the probe
                    # didn't surface. Mark unavailable so it's visible.
                    status = "unavailable"
                    reason = (
                        "polylogue archive_readiness=ready but work_events() "
                        "returned [] — likely stale insight rows; run "
                        "`polylogue doctor --repair --target session_insights`"
                    )
                log.warning(
                    "substrate_promote: ai_work_events empty in window %s–%s (%s: %s)",
                    window_start, window_end, status, reason,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                    status=status, reason=reason, row_count=0,
                    window_start=window_start, window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: AI work events promotion failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

        # ── evidence graph: build + promote ───────────────────────────────────
        if write_evidence_graph:
            try:
                from lynchpin.composite.evidence_graph import build_evidence_graph

                graph = build_evidence_graph(
                    start=window_start, end=window_end, mode="local-fast",
                )
                graph_counts = promote_evidence_graph(
                    conn, refresh_id=refresh_id, graph=graph,
                )
                counts["evidence_graph_nodes"] = graph_counts.get("nodes", 0)
                counts["evidence_graph_edges"] = graph_counts.get("edges", 0)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_EVIDENCE_GRAPH,
                    status="ok", reason=None,
                    row_count=counts["evidence_graph_nodes"],
                    window_start=window_start, window_end=window_end,
                )
            except Exception as exc:
                log.warning("substrate_promote: evidence_graph promotion skipped: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_EVIDENCE_GRAPH,
                    status="error", reason=str(exc), row_count=0,
                    window_start=window_start, window_end=window_end,
                )

        # ── pr_review_rows: promote if active_pr_review_topology.json present ─
        # M.7 (pr_review_topology) is standalone, not in the refresh DAG.
        # When its output file is present (because the user ran it manually
        # or as part of current_state's --github-frontier flag), pick it up.
        # When absent, mark unavailable so the readiness report shows the gap
        # rather than silently masking it.
        if pr_review_file is None:
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                status="unavailable",
                reason="pr_review_file not provided to substrate_promote",
                row_count=0,
            )
        else:
            try:
                pr_rows = list(_load_pr_review_rows(pr_review_file))
            except Exception as exc:
                log.warning("substrate_promote: pr_review hydration failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                    status="error", reason=str(exc), row_count=0,
                )
                pr_rows = []

            if pr_rows:
                try:
                    counts["pr_review_rows"] = promote_pr_review_rows(
                        conn, refresh_id=refresh_id, rows=pr_rows,
                    )
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="ok", reason=None,
                        row_count=counts["pr_review_rows"],
                    )
                except Exception as exc:
                    log.warning("substrate_promote: pr_review promotion failed: %s", exc)
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="error", reason=str(exc), row_count=0,
                    )
            else:
                if not Path(pr_review_file).exists():
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="unavailable",
                        reason="active_pr_review_topology.json missing — run pr_review_topology",
                        row_count=0,
                    )
                else:
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="empty", reason="no PR rows in payload",
                        row_count=0,
                    )

        # ── calendar_events: best-effort promotion from JSONL source ──────────
        try:
            from lynchpin.sources.calendar import iter_events
            from pathlib import Path
            from lynchpin.core.config import get_config

            cal_path = get_config().calendar_jsonl
            events = list(iter_events(start=window_start, end=window_end))
            if events:
                counts["calendar_events"] = promote_calendar_events(
                    conn, refresh_id=refresh_id, events=events,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                    status="ok", reason=None,
                    row_count=counts["calendar_events"],
                    window_start=window_start, window_end=window_end,
                )
            else:
                cal_exists = cal_path.exists()
                status = "unavailable" if not cal_exists else "empty"
                reason = (
                    f"calendar JSONL not found at {cal_path}"
                    if not cal_exists
                    else "no calendar events in window"
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                    status=status, reason=reason, row_count=0,
                    window_start=window_start, window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: calendar promotion skipped: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

    log.info(
        "substrate promotion complete: refresh_id=%s counts=%s",
        refresh_id, counts,
    )
    return counts


def _load_commit_facts(path: str):
    """Hydrate active_commit_facts.json → Iterable[GitCommitFact].

    The JSON schema uses ``timestamp`` (ISO string) for authored_at and stores
    ``path_roots`` as ``dict[str, int]`` (root → change count).  Line counts
    are intentionally absent from the fast active facts surface; they will be
    zero in the substrate row (source: churn_caveat in methodology).
    """
    from lynchpin.sources.git import GitCommitFact

    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("commits", []):
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        path_roots_raw = entry.get("path_roots") or {}
        path_roots_tuple: tuple[str, ...] = (
            tuple(path_roots_raw.keys())
            if isinstance(path_roots_raw, dict)
            else tuple(path_roots_raw)
        )
        yield GitCommitFact(
            repo=entry.get("project") or "",
            commit=entry.get("sha") or "",
            authored_at=authored_at,
            author=entry.get("author") or "",
            subject=entry.get("subject") or "",
            lines_added=0,   # not present in active facts (churn_caveat)
            lines_deleted=0,
            lines_changed=0,
            files_changed=int(entry.get("files_changed") or 0),
            paths=tuple(entry.get("paths") or ()),
            path_roots=path_roots_tuple,
        )


def _load_file_change_facts(path: str):
    """Hydrate active_file_change_facts.json → Iterable[GitFileChangeFact].

    Line counts are absent from the active file-change facts surface for the
    same reason as commits; they are stored as zero.
    """
    from lynchpin.sources.git import GitFileChangeFact

    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("file_changes", []):
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        yield GitFileChangeFact(
            repo=entry.get("project") or "",
            commit=entry.get("sha") or "",
            authored_at=authored_at,
            path=entry.get("path") or "",
            path_root=entry.get("path_root") or "",
            lines_added=0,   # not present in active facts (churn_caveat)
            lines_deleted=0,
            lines_changed=0,
        )


def _load_symbol_change_rows(path: str):
    """Yield active_symbol_changes.json events as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("events", []):
        if isinstance(entry, dict):
            yield entry


def _load_pr_review_rows(path: str):
    """Yield active_pr_review_topology.json prs as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("prs", []):
        if isinstance(entry, dict):
            yield entry


__all__ = [
    "run_substrate_promote",
    "SOURCE_COMMITS",
    "SOURCE_FILE_CHANGES",
    "SOURCE_SYMBOLS",
    "SOURCE_AI_WORK_EVENTS",
    "SOURCE_EVIDENCE_GRAPH",
    "SOURCE_PR_REVIEW",
]
