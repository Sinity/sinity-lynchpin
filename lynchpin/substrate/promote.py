"""Idempotent promoters: pull from lynchpin typed sources → INSERT into DuckDB substrate.

Each promoter is idempotent on ``refresh_id``:
  1. DELETE FROM <table> WHERE refresh_id = ?
  2. Bulk INSERT new rows.

Re-running with the same ``refresh_id`` produces identical row counts.

Schema: ``lynchpin/substrate/schema.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
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
    annotations: Mapping[str, dict[str, Any]] | None = None,
) -> int:
    """INSERT commit rows, idempotent on refresh_id.  Returns rows written.

    ``project_lookup`` is called with the repo name; when omitted the repo name
    is used directly as project.  ``annotations`` is a mapping of commit sha →
    annotation dict (keys: conventional_kind, conventional_scope,
    conventional_signature, breaking_change, github_refs, ai_attribution,
    categories, change_types, classified_files_changed, parent_count,
    default_branch, head).
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

        # Arc 3 enrichment: categories, change_types, etc. from JSON annotations
        categories_raw = a.get("categories")
        categories_json = json.dumps(categories_raw) if isinstance(categories_raw, dict) else "{}"
        change_types_raw = a.get("change_types")
        change_types_json = json.dumps(change_types_raw) if isinstance(change_types_raw, dict) else "{}"
        classified_files = a.get("classified_files_changed")
        parent_count_val = a.get("parent_count")

        rows.append((
            f.commit,                              # sha
            f.repo,                                # repo
            proj,                                  # project
            f.authored_at,                         # authored_at
            f.author,                              # author
            f.subject,                             # subject
            f.lines_added,                         # lines_added
            f.lines_deleted,                       # lines_deleted
            f.lines_changed,                       # lines_changed
            f.files_changed,                       # files_changed
            list(f.paths),                         # paths
            list(f.path_roots),                    # path_roots
            a.get("conventional_kind"),            # conventional_kind
            a.get("conventional_scope"),           # conventional_scope
            a.get("conventional_signature"),       # conventional_signature
            bool(a.get("breaking_change", False)), # breaking_change
            github_refs,                           # github_refs STRUCT
            ai_attribution_json,                   # ai_attribution JSON
            categories_json,                       # categories JSON
            change_types_json,                     # change_types JSON
            int(classified_files) if classified_files is not None else 0,  # classified_files_changed
            int(parent_count_val) if parent_count_val is not None else 1,  # parent_count
            a.get("default_branch"),               # default_branch
            a.get("head"),                         # head
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
                categories, change_types, classified_files_changed,
                parent_count, default_branch, head,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    annotations: Mapping[tuple[str, str], dict[str, Any]] | None = None,
) -> int:
    """INSERT file-change rows, idempotent on refresh_id.

    ``annotations`` is a mapping of (sha, path) → dict with keys
    change_type, status_code, previous_path from the JSON source.
    """
    conn.execute("DELETE FROM file_change_fact WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    ann = annotations or {}
    for f in facts:
        proj = project_lookup(f.repo) if project_lookup else f.repo
        a = ann.get((f.commit, f.path), {})
        change_type = a.get("change_type") or a.get("status_code")
        previous_path = a.get("previous_path")
        rows.append((
            f.commit,        # sha
            f.repo,          # repo
            proj,            # project
            f.authored_at,   # authored_at
            f.path,          # path
            f.path_root,     # path_root
            f.lines_added,   # lines_added
            f.lines_deleted, # lines_deleted
            f.lines_changed, # lines_changed
            change_type,     # change_type (from JSON annotations)
            previous_path,   # previous_path (non-NULL for renames)
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
    projects: Sequence[str] = (),
) -> dict[str, int]:
    """Idempotently promote an EvidenceGraph to substrate.

    Writes one row to evidence_graph_build, then bulk-inserts nodes and edges.
    DELETEs prior rows for the same refresh_id first (child tables first, then
    parent).

    Returns: {"build": 1, "nodes": N, "edges": M}.
    """
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


# ── spotify_daily ─────────────────────────────────────────────────────────────


def promote_spotify_daily(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    streams: Iterable[Any],
) -> int:
    """INSERT spotify_daily rows, idempotent on refresh_id.

    Aggregates SpotifyStream objects by date to produce daily listening
    stats: track count, minutes played, unique artists/tracks, top lists.
    """
    from collections import Counter as _Counter

    conn.execute("DELETE FROM spotify_daily WHERE refresh_id = ?", [refresh_id])

    by_day: dict[date, list[Any]] = {}
    for s in streams:
        d = s.end_time.date() if hasattr(s, 'end_time') else None
        if d is None:
            continue
        by_day.setdefault(d, []).append(s)

    rows: list[tuple[Any, ...]] = []
    for d, day_streams in by_day.items():
        artists = _Counter(s.artist for s in day_streams if hasattr(s, 'artist'))
        tracks = _Counter(s.track for s in day_streams if hasattr(s, 'track'))
        minutes = sum((getattr(s, 'ms_played', 0) or 0) / 60_000 for s in day_streams)
        rows.append((
            d,
            len(day_streams),
            round(minutes, 1),
            len(artists),
            len(tracks),
            [a for a, _ in artists.most_common(5)],
            [t for t, _ in tracks.most_common(5)],
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO spotify_daily (
                date, track_count, minutes_played, unique_artists, unique_tracks,
                top_artists, top_tracks, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_spotify_daily: %d days for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


# ── machine_metric_sample ────────────────────────────────────────────────────


def promote_machine_metric_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_metric_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_metric_sample WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, boot_id, source, source_schema_version,
                cpu_package_w, cpu_core_w, cpu_pkg_c, cpu_max_core_c,
                gpu_power_w, gpu_fan_pct, gpu_temp_c, gpu_util_pct,
                gpu_pstate, gpu_pcie_gen, gpu_pcie_width,
                load_1m, mem_avail_mb, io_psi_some_avg10, io_psi_full_avg10,
                io_psi_some_avg60, io_psi_some_avg300, io_psi_some_total_us,
                io_psi_full_avg60, io_psi_full_avg300, io_psi_full_total_us,
                cpu_psi_some_avg60, cpu_psi_some_avg300, cpu_psi_some_total_us,
                memory_psi_some_avg60, memory_psi_some_avg300, memory_psi_some_total_us,
                memory_psi_full_avg60, memory_psi_full_avg300, memory_psi_full_total_us,
                latency_oversleep_ms, dstate_task_count, gap_codes, refresh_id
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            sample.source,
            int(sample.source_schema_version),
            sample.cpu_package_w,
            sample.cpu_core_w,
            sample.cpu_pkg_c,
            sample.cpu_max_core_c,
            sample.gpu_power_w,
            sample.gpu_fan_pct,
            sample.gpu_temp_c,
            sample.gpu_util_pct,
            sample.gpu_pstate,
            sample.gpu_pcie_gen,
            sample.gpu_pcie_width,
            sample.load_1m,
            sample.mem_avail_mb,
            sample.io_psi_some_avg10,
            sample.io_psi_full_avg10,
            sample.io_psi_some_avg60,
            sample.io_psi_some_avg300,
            sample.io_psi_some_total_us,
            sample.io_psi_full_avg60,
            sample.io_psi_full_avg300,
            sample.io_psi_full_total_us,
            sample.cpu_psi_some_avg60,
            sample.cpu_psi_some_avg300,
            sample.cpu_psi_some_total_us,
            sample.memory_psi_some_avg60,
            sample.memory_psi_some_avg300,
            sample.memory_psi_some_total_us,
            sample.memory_psi_full_avg60,
            sample.memory_psi_full_avg300,
            sample.memory_psi_full_total_us,
            sample.latency_oversleep_ms,
            sample.dstate_task_count,
            list(sample.gap_codes),
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_metric_samples: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_service_states(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    states: Iterable[Any],
) -> int:
    """INSERT machine_service_state rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_service_state WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_service_state (
                observed_at, host, boot_id, unit, scope,
                active_state, sub_state, main_pid, control_group,
                memory_current_bytes, cpu_usage_nsec, io_read_bytes, io_write_bytes,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for state in states:
        rows.append((
            state.observed_at,
            state.host,
            state.boot_id,
            state.unit,
            state.scope,
            state.active_state,
            state.sub_state,
            state.main_pid,
            state.control_group,
            state.memory_current_bytes,
            state.cpu_usage_nsec,
            state.io_read_bytes,
            state.io_write_bytes,
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_service_states: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_gpu_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_gpu_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_gpu_sample WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_gpu_sample (
                observed_at, host, boot_id, source,
                gpu_power_w, gpu_power_limit_w, gpu_temp_c, gpu_fan_pct,
                gpu_util_pct, gpu_mem_util_pct, gpu_clock_mhz, gpu_mem_clock_mhz,
                gpu_pstate, gpu_pcie_gen, gpu_pcie_width, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            sample.source,
            sample.gpu_power_w,
            sample.gpu_power_limit_w,
            sample.gpu_temp_c,
            sample.gpu_fan_pct,
            sample.gpu_util_pct,
            sample.gpu_mem_util_pct,
            sample.gpu_clock_mhz,
            sample.gpu_mem_clock_mhz,
            sample.gpu_pstate,
            sample.gpu_pcie_gen,
            sample.gpu_pcie_width,
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_gpu_samples: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_network_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_network_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_network_sample WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            int(sample.source_schema_version),
            sample.interface,
            sample.gateway_ip,
            json.dumps(sample.ping),
            json.dumps(sample.bloat) if sample.bloat is not None else None,
            json.dumps(sample.iface),
            json.dumps(sample.nic),
            json.dumps(sample.tcp),
            sample.dns_ms,
            sample.pmtu_1492,
            json.dumps(sample.conntrack),
            list(sample.gap_codes),
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO machine_network_sample (
                observed_at, host, boot_id, source_schema_version,
                interface, gateway_ip, ping, bloat, iface, nic, tcp,
                dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_machine_network_samples: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


def promote_machine_experiment_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    runs: Iterable[Any],
) -> int:
    """INSERT machine_experiment_run rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_experiment_run WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for run in runs:
        rows.append((
            run.run_id,
            run.host,
            run.workload,
            list(run.command),
            run.cwd,
            run.started_at,
            run.ended_at,
            run.exit_status,
            run.service_profile,
            run.cache_profile,
            json.dumps(run.planned_treatment),
            run.git_root,
            run.git_head,
            run.git_branch,
            run.git_dirty,
            json.dumps(run.pre_state),
            json.dumps(run.post_state),
            list(run.notes),
            str(run.manifest_path),
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, cwd,
                started_at, ended_at, exit_status,
                service_profile, cache_profile, planned_treatment,
                git_root, git_head, git_branch, git_dirty,
                pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
    log.debug("promote_machine_experiment_runs: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


__all__ = [
    "promote_calendar_events",
    "promote_commits",
    "promote_file_changes",
    "promote_ai_work_events",
    "promote_machine_experiment_runs",
    "promote_machine_gpu_samples",
    "promote_machine_metric_samples",
    "promote_machine_network_samples",
    "promote_machine_service_states",
    "promote_spotify_daily",
    "promote_symbol_changes",
    "promote_pr_review_rows",
    "promote_evidence_graph",
]
