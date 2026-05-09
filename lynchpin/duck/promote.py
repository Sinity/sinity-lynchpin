"""Idempotent promoters: pull from lynchpin typed sources → INSERT into DuckDB substrate.

Each promoter is idempotent on ``refresh_id``:
  1. DELETE FROM <table> WHERE refresh_id = ?
  2. Bulk INSERT new rows.

Re-running with the same ``refresh_id`` produces identical row counts.

Schema: ``lynchpin/duck/schema.py`` (phase 2.1 tables).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_iso(value: str | None) -> datetime | None:
    """Parse ISO-8601 string → datetime (UTC-aware).  Returns None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── commit_fact ───────────────────────────────────────────────────────────────


def promote_commits(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    facts: Iterable[Any],  # Iterable[GitCommitFact]
    project_lookup: Callable[[str], str | None] | None = None,
    annotations: Mapping[str, dict] | None = None,
) -> int:
    """INSERT commit rows, idempotent on refresh_id.  Returns rows written.

    ``project_lookup`` is called with the repo name; when omitted the repo name
    is used directly as project.  ``annotations`` is a mapping of commit sha →
    annotation dict (keys: conventional_kind, conventional_scope,
    conventional_signature, breaking_change, github_refs, ai_attribution).
    """
    conn.execute("DELETE FROM commit_fact WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    ann = annotations or {}
    for f in facts:
        proj = project_lookup(f.repo) if project_lookup else f.repo
        a = ann.get(f.commit, {})
        github_refs_raw = a.get("github_refs")
        # DuckDB accepts a Python dict for STRUCT columns.
        if isinstance(github_refs_raw, dict):
            github_refs = github_refs_raw
        elif github_refs_raw is not None:
            # Defensive: if a string was passed, ignore it.
            github_refs = None
        else:
            github_refs = None

        ai_attribution = a.get("ai_attribution")
        ai_attribution_json = json.dumps(ai_attribution) if ai_attribution is not None else None

        rows.append((
            f.commit,                              # sha
            f.repo,                                # repo
            proj,                                  # project
            f.authored_at,                         # authored_at  TIMESTAMPTZ
            f.author,                              # author
            f.subject,                             # subject
            f.lines_added,                         # lines_added
            f.lines_deleted,                       # lines_deleted
            f.lines_changed,                       # lines_changed
            f.files_changed,                       # files_changed
            list(f.paths),                         # paths  VARCHAR[]
            list(f.path_roots),                    # path_roots  VARCHAR[]
            a.get("conventional_kind"),            # conventional_kind
            a.get("conventional_scope"),           # conventional_scope
            a.get("conventional_signature"),       # conventional_signature
            bool(a.get("breaking_change", False)), # breaking_change
            github_refs,                           # github_refs STRUCT
            ai_attribution_json,                   # ai_attribution JSON
            refresh_id,                            # refresh_id
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO commit_fact (
                sha, repo, project, authored_at, author, subject,
                lines_added, lines_deleted, lines_changed, files_changed,
                paths, path_roots,
                conventional_kind, conventional_scope, conventional_signature,
                breaking_change, github_refs, ai_attribution,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_commits: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


# ── file_change_fact ──────────────────────────────────────────────────────────


def promote_file_changes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    facts: Iterable[Any],  # Iterable[GitFileChangeFact]
    project_lookup: Callable[[str], str | None] | None = None,
) -> int:
    """INSERT file-change rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM file_change_fact WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for f in facts:
        proj = project_lookup(f.repo) if project_lookup else f.repo
        rows.append((
            f.commit,        # sha
            f.repo,          # repo
            proj,            # project
            f.authored_at,   # authored_at  TIMESTAMPTZ
            f.path,          # path
            f.path_root,     # path_root
            f.lines_added,   # lines_added
            f.lines_deleted, # lines_deleted
            f.lines_changed, # lines_changed
            None,            # change_type (not on GitFileChangeFact today)
            None,            # previous_path
            refresh_id,      # refresh_id
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO file_change_fact (
                sha, repo, project, authored_at, path, path_root,
                lines_added, lines_deleted, lines_changed,
                change_type, previous_path, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_file_changes: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


# ── ai_work_event ─────────────────────────────────────────────────────────────


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
    conn.execute("DELETE FROM ai_work_event WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for ev in events:
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
            polylogue_confidence = float(ev.confidence) if ev.confidence is not None else 0.0
            overlay_kind = None
            overlay_confidence = None

        rows.append((
            ev.event_id,                           # event_id
            ev.conversation_id,                    # conversation_id
            ev.provider,                           # provider
            proj,                                  # project
            kind,                                  # kind
            kind_confidence,                       # kind_confidence
            kind_tier,                             # kind_tier
            kind_source,                           # kind_source
            polylogue_kind,                        # polylogue_kind
            polylogue_confidence,                  # polylogue_confidence
            overlay_kind,                          # overlay_kind
            overlay_confidence,                    # overlay_confidence
            list(ev.file_paths),                   # file_paths  VARCHAR[]
            list(ev.tools_used),                   # tools_used  VARCHAR[]
            ev.start,                              # start_ts  TIMESTAMPTZ (nullable)
            ev.end,                                # end_ts  TIMESTAMPTZ (nullable)
            int(ev.duration_ms),                   # duration_ms
            ev.summary or None,                    # summary
            refresh_id,                            # refresh_id
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO ai_work_event (
                event_id, conversation_id, provider, project,
                kind, kind_confidence, kind_tier, kind_source,
                polylogue_kind, polylogue_confidence,
                overlay_kind, overlay_confidence,
                file_paths, tools_used,
                start_ts, end_ts, duration_ms,
                summary, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_ai_work_events: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


# ── symbol_change ─────────────────────────────────────────────────────────────


def promote_symbol_changes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """INSERT symbol_change rows from build_active_symbol_changes events[].

    Each mapping must carry: sha, project, date (str), path, change_type,
    qualified_name, symbol_kind, exported, breaking_candidate.

    Missing keys default gracefully so callers can pass the raw dicts from
    the JSON payload without pre-processing.
    """
    conn.execute("DELETE FROM symbol_change WHERE refresh_id = ?", [refresh_id])

    tuples: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str, str]] = set()  # dedupe (sha, path, qualified_name)
    for r in rows:
        sha = r.get("sha") or ""
        project = r.get("project") or ""
        raw_date = r.get("date")
        if isinstance(raw_date, str):
            try:
                row_date = date.fromisoformat(raw_date)
            except ValueError:
                row_date = None
        elif isinstance(raw_date, date):
            row_date = raw_date
        else:
            row_date = None
        if row_date is None:
            continue  # Skip rows without a parseable date.

        key = (sha, r.get("path") or "", r.get("qualified_name") or "")
        if key in seen:
            continue
        seen.add(key)
        tuples.append((
            sha,
            project,
            row_date,
            r.get("path") or "",
            (r.get("change_type") or "").upper() or "M",
            r.get("qualified_name") or "",
            r.get("symbol_kind") or "unknown",
            bool(r.get("exported", False)),
            bool(r.get("breaking_candidate", False)),
            refresh_id,
        ))

    if tuples:
        conn.executemany(
            """
            INSERT INTO symbol_change (
                sha, project, date, path, change_type,
                qualified_name, symbol_kind, exported, breaking_candidate,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuples,
        )
    log.debug("promote_symbol_changes: %d rows for refresh_id=%s", len(tuples), refresh_id)
    return len(tuples)


# ── pr_review_row ─────────────────────────────────────────────────────────────


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
    conn.execute("DELETE FROM pr_review_row WHERE refresh_id = ?", [refresh_id])

    tuples: list[tuple[Any, ...]] = []
    for r in rows:
        tuples.append((
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
            refresh_id,
        ))

    if tuples:
        conn.executemany(
            """
            INSERT INTO pr_review_row (
                project, number, title, state, url, author,
                created_at, closed_at, merged_at,
                review_count, review_decisions,
                review_round_count, reviewer_count, reviewers,
                review_comment_count, top_level_comment_count,
                changes_requested_count, approval_count, dismissed_count,
                time_to_first_review_minutes, time_to_close_minutes,
                time_to_merge_minutes, final_decision, friction_signals,
                refresh_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?
            )
            """,
            tuples,
        )
    log.debug("promote_pr_review_rows: %d rows for refresh_id=%s", len(tuples), refresh_id)
    return len(tuples)

# ── evidence_graph ────────────────────────────────────────────────────────────


def promote_evidence_graph(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    graph: Any,  # EvidenceGraph — imported lazily to avoid circular imports
    projects: "Sequence[str]" = (),
) -> dict[str, int]:
    """Idempotently promote an EvidenceGraph to substrate.

    Writes one row to evidence_graph_build, then bulk-inserts nodes and edges.
    DELETEs prior rows for the same refresh_id first (child tables first, then
    parent).

    Returns: {"build": 1, "nodes": N, "edges": M}.
    """
    # Lazy imports to avoid circular dependency — promote.py is consumed by
    # composite modules that themselves import from composite.evidence_graph.
    from collections.abc import Sequence  # noqa: PLC0415

    # ── idempotent delete (children first) ────────────────────────────────
    conn.execute("DELETE FROM evidence_edge WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_node WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_graph_build WHERE refresh_id = ?", [refresh_id])

    # ── evidence_graph_build row ──────────────────────────────────────────
    caveats_json = json.dumps([
        {"source": c.source, "status": c.status, "message": c.message}
        for c in graph.caveats
    ])
    mode_str = graph.mode if isinstance(graph.mode, str) else str(graph.mode)
    conn.execute(
        """
        INSERT INTO evidence_graph_build (
            refresh_id, start_date, end_date, mode, projects,
            node_count, edge_count, caveats, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            graph.start,
            graph.end,
            mode_str,
            list(projects),
            len(graph.nodes),
            len(graph.edges),
            caveats_json,
            graph.generated_at,
        ],
    )

    # ── evidence_node rows ────────────────────────────────────────────────
    node_rows: list[tuple[Any, ...]] = []
    for node in graph.nodes:
        payload_json = json.dumps(node.payload) if node.payload is not None else None

        node_caveats_json = json.dumps([
            {"source": c.source, "status": c.status, "message": c.message}
            for c in node.caveats
        ])

        # DuckDB accepts a plain Python dict for STRUCT columns — field names
        # must match the STRUCT definition exactly.  Pass None if no provenance.
        if node.provenance is not None:
            p = node.provenance
            provenance_struct: dict[str, Any] | None = {
                "source": p.source,
                "cost": p.cost if isinstance(p.cost, str) else str(p.cost),
                "path": p.path,
                "generated_at": p.generated_at,
                "note": p.note,
            }
        else:
            provenance_struct = None

        kind_str = node.kind if isinstance(node.kind, str) else str(node.kind)

        node_rows.append((
            refresh_id,          # refresh_id
            node.id,             # id
            kind_str,            # kind
            node.source,         # source
            node.date,           # date  DATE
            node.project,        # project  VARCHAR nullable
            node.summary,        # summary
            node.start,          # start_ts  TIMESTAMPTZ nullable
            node.end,            # end_ts    TIMESTAMPTZ nullable
            node.url,            # url  VARCHAR nullable
            payload_json,        # payload  JSON nullable
            provenance_struct,   # provenance  STRUCT nullable
            node_caveats_json,   # caveats  JSON
        ))

    if node_rows:
        conn.executemany(
            """
            INSERT INTO evidence_node (
                refresh_id, id, kind, source, date, project, summary,
                start_ts, end_ts, url, payload, provenance, caveats
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            node_rows,
        )

    # ── evidence_edge rows ────────────────────────────────────────────────
    edge_rows: list[tuple[Any, ...]] = []
    for edge in graph.edges:
        relation_str = edge.relation if isinstance(edge.relation, str) else str(edge.relation)
        edge_rows.append((
            refresh_id,         # refresh_id
            edge.source_id,     # source_id
            edge.target_id,     # target_id
            relation_str,       # relation
            edge.evidence,      # evidence
            float(edge.weight), # weight  DOUBLE
        ))

    if edge_rows:
        conn.executemany(
            """
            INSERT INTO evidence_edge (
                refresh_id, source_id, target_id, relation, evidence, weight
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )

    log.debug(
        "promote_evidence_graph: refresh_id=%s nodes=%d edges=%d",
        refresh_id, len(node_rows), len(edge_rows),
    )
    return {"build": 1, "nodes": len(node_rows), "edges": len(edge_rows)}


# ── calendar_event ─────────────────────────────────────────────────────────────


def promote_calendar_events(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    events: Iterable[Any],
) -> int:
    """INSERT calendar_event rows, idempotent on refresh_id (Arc M.12)."""
    conn.execute("DELETE FROM calendar_event WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for ev in events:
        rows.append((
            ev.uid or "",
            getattr(ev, "calendar", None),
            ev.summary or "",
            ev.start_at,
            ev.end_at,
            bool(getattr(ev, "all_day", False)),
            getattr(ev, "location", ""),
            list(getattr(ev, "attendees", []) or []),
            getattr(ev, "description", None),
            getattr(ev, "status", None),
            getattr(ev, "created_at", None),
            getattr(ev, "updated_at", None),
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO calendar_event (
                uid, calendar, summary, start_at, end_at, all_day,
                location, attendees, description, status,
                created_at, updated_at, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_calendar_events: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


__all__ = [
    "promote_calendar_events",
    "promote_commits",
    "promote_file_changes",
    "promote_ai_work_events",
    "promote_symbol_changes",
    "promote_pr_review_rows",
    "promote_evidence_graph",
]
