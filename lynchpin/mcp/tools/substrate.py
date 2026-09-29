"""Substrate-backed MCP tools: raw SQL access and evidence-graph metadata.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""

from dataclasses import asdict
from typing import Any

from lynchpin.substrate.integrity import (
    integrity_caveats,
    measure_graph_integrity,
    unavailable_graph_integrity,
)
from lynchpin.mcp.tools._utils import (
    _record_materialization_caveat,
    ensure_substrate_materialized_for_read,
    half_open_date_window,
    pinned_materialization_for_read,
)
from lynchpin.mcp.tools._utils import json_safe as _json_safe
from lynchpin.mcp.tools._utils import latest_materialized_refresh_id

# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

_MAX_ROWS_HARD_CAP = 10_000


class QueryRefreshMismatch(Exception):
    """Raised when a query requires a different serving generation."""

    def __init__(
        self,
        *,
        expected_refresh_id: str,
        actual_refresh_id: str | None,
        serving_kind: str,
    ) -> None:
        self.expected_refresh_id = expected_refresh_id
        self.actual_refresh_id = actual_refresh_id
        self.serving_kind = serving_kind
        super().__init__(
            "serving refresh does not match the requested refresh: "
            f"expected {expected_refresh_id!r}, got {actual_refresh_id!r}"
        )


class QueryPublicationMismatch(Exception):
    """A requested exact publication is not the selected serving publication."""

    def __init__(self, *, expected_publication_id: str, actual_publication_id: str | None) -> None:
        self.expected_publication_id = expected_publication_id
        self.actual_publication_id = actual_publication_id
        super().__init__(
            "serving publication does not match the requested publication: "
            f"expected {expected_publication_id!r}, got {actual_publication_id!r}"
        )


def _is_select_only(sql: str) -> bool:
    """Return True only for one DuckDB-parsed SELECT statement.

    Prefix and keyword checks cannot distinguish SQL syntax from text inside
    string literals, and they allow a read query followed by another
    statement. DuckDB's parser identifies both statement boundaries and the
    statement kind without executing the query.
    """
    import duckdb

    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error:
        return False
    return len(statements) == 1 and statements[0].type == duckdb.StatementType.SELECT


def _best_commit_ai_join_refresh_id(conn: Any) -> str | None:
    row = conn.execute(
        """
        WITH commit_counts AS (
            SELECT refresh_id, COUNT(*) AS commit_count
            FROM commit_fact
            GROUP BY refresh_id
        ),
        ai_counts AS (
            SELECT refresh_id, COUNT(*) AS ai_count
            FROM ai_work_event
            GROUP BY refresh_id
        )
        SELECT c.refresh_id
        FROM commit_counts c
        JOIN ai_counts a USING (refresh_id)
        ORDER BY LEAST(c.commit_count, a.ai_count) DESC,
                 c.commit_count + a.ai_count DESC,
                 c.refresh_id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row[0]) if row else None


def _query_tables(conn: Any, sql: str) -> set[str] | None:
    """Return relations the statement names, or None when DuckDB cannot say."""
    try:
        return {str(name) for name in conn.get_table_names(sql)}
    except Exception:  # noqa: BLE001 - relevance is advisory; the query itself still runs.
        return None


def _compact_source_status(
    status_rows: list[dict[str, Any]], tables: set[str] | None
) -> dict[str, Any]:
    """Keep status rows for the sources a query reads and count the rest.

    A source is relevant only through a recorded mapping: the canonical
    table-to-source registry, or a status row named after the table itself.
    Unmapped tables are listed rather than attributed to a guessed source.
    """
    from lynchpin.core.substrate_sources import SUBSTRATE_TABLE_SOURCE

    recorded = {str(row["source"]) for row in status_rows}
    relevant: set[str] = set()
    unmapped: list[str] = []
    if tables is None:
        return {
            "relevant_source_status": status_rows,
            "relevance": "unknown: referenced tables could not be parsed; all sources listed",
        }
    for table in sorted(tables):
        source = SUBSTRATE_TABLE_SOURCE.get(table) or (table if table in recorded else None)
        if source is None:
            unmapped.append(table)
        else:
            relevant.add(source)
    other_counts: dict[str, int] = {}
    for row in status_rows:
        if row["source"] not in relevant:
            status = str(row["status"])
            other_counts[status] = other_counts.get(status, 0) + 1
    return {
        "relevant_source_status": [row for row in status_rows if row["source"] in relevant],
        "unmapped_tables": unmapped,
        "other_source_status_counts": other_counts,
    }


def _serving_source_status(conn: Any, refresh_id: str | None) -> list[dict[str, Any]]:
    if refresh_id is None:
        return []
    rows = conn.execute(
        "SELECT source, kind, status, reason, row_count, window_start, window_end, "
        "recorded_at FROM substrate_source_status WHERE refresh_id = ? "
        "ORDER BY source, kind",
        [refresh_id],
    ).fetchall()
    columns = (
        "source", "kind", "status", "reason", "row_count", "window_start",
        "window_end", "recorded_at",
    )
    return [dict(zip(columns, (_json_safe(value) for value in row))) for row in rows]

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def query_substrate(
    sql: str,
    parameters: list[Any] | None = None,
    max_rows: int = 1000,
    expected_refresh_id: str | None = None,
    expected_publication_id: str | None = None,
    detail: bool = False,
) -> dict[str, Any]:
    """Execute a read-only SELECT against the lynchpin substrate.

    Allowed: SELECT statements and CTEs (WITH … SELECT).
    Rejected: multiple statements and all statements other than SELECT,
    including DDL/DML, COPY, SET, and administrative statements.

    The connection is opened with ``read_only=True`` so DuckDB enforces the
    constraint at the engine level in addition to the parser check.
    ``expected_refresh_id`` checks the logical promotion. For an exact serving
    content pin, ``expected_publication_id`` checks the selected publication
    under the same lock.

    Raw tables keep every retained promotion, so rows are not one current
    snapshot: ``grain`` states the history each referenced table carries.
    Typed readers select one coherent product; raw SQL reports history.

    The default ``freshness`` block is compact: the served generation, the
    inspection status, the status rows of sources the query's tables map to,
    and per-status counts for every other source. ``detail=True`` adds the
    full source-status list and the inspection's high-water and coverage.

    Returns:
        {
            "columns": ["col1", "col2", ...],
            "rows": [[val, ...], ...],
            "row_count": N,
            "truncated": bool,
            "serving": {"kind": "canonical | read_snapshot", "refresh_id": str | None,
                        "publication_id": str | None},
            "grain": {table: {"history": "retained_refreshes | lineage_partitions
                              | unversioned | view", ...}},
            "freshness": {"status": str, "reason": str,
                          "serving_source_status_refresh_id": str | None,
                          "relevant_source_status": list,
                          "unmapped_tables": list,
                          "other_source_status_counts": dict,
                          # detail=True only:
                          "source_high_water": dict, "coverage": dict,
                          "serving_source_status": list},
        }

    max_rows is capped at 10 000.
    """
    from lynchpin.substrate.connection import substrate_path

    if not _is_select_only(sql):
        raise ValueError(
            "Only SELECT statements are permitted. "
            "Detected multiple statements or a non-SELECT statement."
        )
    if max_rows < 1:
        raise ValueError("max_rows must be positive")

    effective_max = min(max_rows, _MAX_ROWS_HARD_CAP)
    params = list(parameters) if parameters else []

    inspected = ensure_substrate_materialized_for_read(caller="query_substrate")
    detail_keys = ("source_high_water", "coverage") if detail else ()
    freshness = {
        key: inspected[key]
        for key in ("status", "reason", *detail_keys)
        if key in inspected
    }
    from lynchpin.substrate.connection import _same_resource, serving_generation
    from lynchpin.substrate.snapshots import raw_table_grain

    path = substrate_path()
    with serving_generation(path) as serving:
        conn = serving.connection
        conn.execute("SET enable_external_access = false")
        conn.execute("SET autoinstall_known_extensions = false")
        conn.execute("SET autoload_known_extensions = false")
        refresh_id = latest_materialized_refresh_id(conn, caller="query_substrate")
        serving_kind = (
            "read_snapshot" if _same_resource(serving.database_path, serving.snapshot_path)
            else "canonical"
        )
        status_rows = _serving_source_status(conn, refresh_id)
        tables = _query_tables(conn, sql)
        grain = raw_table_grain(conn, tables or set())
        freshness["serving_source_status_refresh_id"] = refresh_id
        freshness.update(_compact_source_status(status_rows, tables))
        if detail:
            freshness["serving_source_status"] = status_rows
        inspected_refresh_id = inspected.get("source_high_water", {}).get("serving_refresh_id")
        if freshness.get("status") == "ready" and inspected_refresh_id != refresh_id:
            freshness = {
                **freshness,
                "status": "blocked",
                "reason": "serving refresh changed after freshness inspection",
            }
            _record_materialization_caveat({
                "caller": "query_substrate",
                "status": "blocked",
                "reason": freshness["reason"],
                "inspected_refresh_id": inspected_refresh_id,
                "served_refresh_id": refresh_id,
            })
        if expected_refresh_id is not None and expected_refresh_id != refresh_id:
            raise QueryRefreshMismatch(
                expected_refresh_id=expected_refresh_id,
                actual_refresh_id=refresh_id,
                serving_kind=serving_kind,
            )
        if expected_publication_id is not None and expected_publication_id != serving.publication_id:
            raise QueryPublicationMismatch(
                expected_publication_id=expected_publication_id,
                actual_publication_id=serving.publication_id,
            )
        result = conn.execute(sql, params)
        columns = [desc[0] for desc in result.description]
        # Fetch one extra row to detect truncation without a separate COUNT query
        raw_rows = result.fetchmany(effective_max + 1)

    truncated = len(raw_rows) > effective_max
    rows = raw_rows[:effective_max]

    return {
        "columns": columns,
        "rows": [_json_safe(list(row)) for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "serving": {"kind": serving_kind, "refresh_id": refresh_id,
                    "publication_id": serving.publication_id},
        "grain": grain,
        "freshness": freshness,
    }


def list_substrate_tables() -> list[dict[str, Any]]:
    """List substrate tables with their column names and types.

    Returns:
        [{"table": str, "columns": [{"name": str, "type": str}]}]

    Tables are returned in alphabetical order. Only user tables are included
    (information_schema and pg_catalog system tables are excluded).
    """
    from lynchpin.substrate.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        table_rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()

        result = []
        for (table_name,) in table_rows:
            col_rows = conn.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'main'
                  AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
            result.append(
                {
                    "table": table_name,
                    "columns": [
                        {"name": col_name, "type": data_type}
                        for col_name, data_type in col_rows
                    ],
                }
            )

    return result


def substrate_readiness_report() -> dict[str, Any]:
    """Aggregate per-source readiness across the latest materialized substrate snapshot.

    One stop: schema version, latest materialized snapshot ID, per-source status with
    age-since-last-success, evidence-graph caveats, and the high-level signal
    "is the substrate trustworthy for analysis right now."

    Returns:
        {
            "substrate_version": int,
            "latest_materialized_refresh_id": str | None,
            "latest_recorded_at": "ISO datetime" | None,
            "sources": [
                {
                    "source": str,
                    "status": "ok | empty | unavailable | error",
                    "row_count": int,
                    "reason": str | None,
                    "window_start": "YYYY-MM-DD" | None,
                    "window_end": "YYYY-MM-DD" | None,
                    "recorded_at": "ISO datetime",
                }
            ],
            "evidence_graph": {
                "refresh_id": str,
                "node_count": int,
                "edge_count": int,
                "caveats": [...],
                "generated_at": "ISO datetime",
            } | None,
            "summary": {
                "ok": int,           # source count by status
                "empty": int,
                "unavailable": int,
                "error": int,
                "trustworthy": bool, # true iff zero unavailable+error
            }
        }

    Returns {"substrate_version": ..., "latest_materialized_refresh_id": null,
    "sources": [], "summary": {...all-zero}} when the substrate has no promote
    history yet.
    """
    from lynchpin.materialization import substrate_materialization_snapshot
    from lynchpin.substrate.connection import (
        generation_refresh_id,
        serving_generation,
        substrate_path,
        substrate_read_snapshot_path,
    )

    path = substrate_path()
    snapshot_path = substrate_read_snapshot_path()
    with serving_generation(path) as observation:
        conn = observation.connection
        canonical_generation = generation_refresh_id(path)
        snapshot_generation = generation_refresh_id(snapshot_path)
        if canonical_generation is not None:
            serving = {
                "state": "ready",
                "kind": "canonical",
                "refresh_id": canonical_generation,
            }
        elif snapshot_generation is not None:
            serving = {
                "state": "degraded_previous_generation",
                "kind": "read_snapshot",
                "refresh_id": snapshot_generation,
            }
        else:
            serving = {"state": "unavailable", "kind": None, "refresh_id": None}
        serving_path = observation.database_path
        # ── substrate version ──────────────────────────────────────────────
        version_row = conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'version'"
        ).fetchone()
        substrate_version = int(version_row[0]) if version_row else None

        # ── latest materialized snapshot ID ────────────────────────────────
        from lynchpin.substrate.snapshots import latest_materialized_snapshot, latest_promotion_snapshot

        latest_row = latest_materialized_snapshot(conn, caller="substrate_readiness_report")

        if latest_row is None:
            latest_attempt = latest_promotion_snapshot(
                conn,
                caller="substrate_readiness_report",
            )
            if latest_attempt is None:
                materialization = substrate_materialization_snapshot(serving_path).to_json()
                return {
                    "substrate_version": substrate_version,
                    "latest_materialized_refresh_id": None,
                    "latest_recorded_at": None,
                    "latest_available_refresh_id": None,
                    "latest_available_status": None,
                    "serving": serving,
                    "sources": [],
                    "evidence_graph": None,
                    "summary": {
                        "ok": 0, "empty": 0, "unavailable": 0, "error": 0,
                        "trustworthy": False,
                    },
                    "materialization": materialization,
                }
            latest_refresh_id, latest_recorded_at, promotion_status, promotion_reason = latest_attempt
            latest_materialized_id = None
        else:
            latest_refresh_id, latest_recorded_at = latest_row
            promotion_status = "ok"
            promotion_reason = None
            latest_materialized_id = str(latest_refresh_id)

        materialization = substrate_materialization_snapshot(
            serving_path,
            latest_materialized_refresh_id=latest_materialized_id,
            latest_recorded_at=latest_recorded_at,
            latest_available_refresh_id=str(latest_refresh_id),
            latest_available_recorded_at=latest_recorded_at,
            latest_available_status=promotion_status,
            latest_available_reason=promotion_reason,
        ).to_json()

        # ── per-source status for that refresh ─────────────────────────────
        rows = conn.execute(
            "SELECT source, kind, status, reason, row_count, window_start, "
            "window_end, recorded_at "
            "FROM substrate_source_status WHERE refresh_id = ? "
            "ORDER BY kind, source",
            [latest_refresh_id],
        ).fetchall()

        sources = [
            {
                "source": row[0],
                "kind": row[1],
                "status": row[2],
                "reason": row[3],
                "row_count": row[4],
                "window_start": _json_safe(row[5]),
                "window_end": _json_safe(row[6]),
                "recorded_at": _json_safe(row[7]),
            }
            for row in rows
        ]

        # ── evidence graph for the same refresh, if present ────────────────
        eg_row = conn.execute(
            "SELECT refresh_id, node_count, edge_count, caveats, generated_at "
            "FROM evidence_graph_build WHERE refresh_id = ?",
            [latest_refresh_id],
        ).fetchone()
        if eg_row is not None:
            import json as _json
            try:
                caveats = _json.loads(eg_row[3]) if eg_row[3] else []
            except (TypeError, ValueError):
                caveats = []
            evidence_graph = {
                "refresh_id": eg_row[0],
                "node_count": eg_row[1],
                "edge_count": eg_row[2],
                "caveats": caveats,
                "graph_integrity": measure_graph_integrity(conn, str(eg_row[0])),
                "generated_at": _json_safe(eg_row[4]),
            }
        else:
            evidence_graph = None

    summary = {
        "ok": 0,
        "empty": 0,
        "unavailable": 0,
        "error": 0,
        "by_kind": {},
    }
    for source in sources:
        s = source["status"]
        if s in summary:
            summary[s] += 1
        kind = source.get("kind") or "stage"
        by_kind = summary["by_kind"].setdefault(
            kind,
            {"ok": 0, "empty": 0, "unavailable": 0, "error": 0},
        )
        if s in by_kind:
            by_kind[s] += 1
    summary["trustworthy"] = (
        promotion_status == "ok"
        and summary["unavailable"] == 0
        and summary["error"] == 0
        and len(sources) > 0
    )

    return {
        "substrate_version": substrate_version,
        "latest_materialized_refresh_id": latest_materialized_id,
        "latest_recorded_at": _json_safe(latest_recorded_at),
        "latest_available_refresh_id": str(latest_refresh_id),
        "latest_available_status": promotion_status,
        "latest_available_reason": promotion_reason,
        "serving": serving,
        "sources": sources,
        "evidence_graph": evidence_graph,
        "summary": summary,
        "materialization": materialization,
    }


def substrate_source_status(
    refresh_id: str | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Per-source readiness rows from the most recent (or specific) promote run.

    Surfaces the outcome of every source in the substrate-promote step:
    ``ok`` (rows present), ``empty`` (source ran but yielded nothing in the
    window — legitimate), ``unavailable`` (source/file missing — typically
    a stale upstream archive), or ``error`` (exception during promote).

    This distinguishes silent failures (e.g., polylogue's session insights
    are stale → ``ai_work_events`` returns 0 with no error) from successful
    promotes that genuinely had nothing to record.

    Parameters:
        refresh_id: exact ID. Default: most recent promote run.
        status:     filter to rows with this status (e.g. "unavailable").

    Returns:
        [{"refresh_id", "source", "status", "reason", "row_count",
          "window_start", "window_end", "recorded_at"}], ordered by source.
    """
    from lynchpin.substrate.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = latest_materialized_refresh_id(conn, caller="substrate_source_status")
            if refresh_id is None:
                return []

        sql = (
            "SELECT refresh_id, source, kind, status, reason, row_count, "
            "window_start, window_end, recorded_at "
            "FROM substrate_source_status WHERE refresh_id = ?"
        )
        params: list[Any] = [refresh_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY kind, source"

        rows = conn.execute(sql, params).fetchall()

    cols = [
        "refresh_id", "source", "kind", "status", "reason", "row_count",
        "window_start", "window_end", "recorded_at",
    ]
    return [
        {c: _json_safe(v) for c, v in zip(cols, row)}
        for row in rows
    ]


def contract_coverage(
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Canonical dataset coverage and gaps for an optional date window."""
    from datetime import date as _date
    from datetime import timedelta

    from lynchpin.materialization import audit_materialization, ensure_materialized, materialized_dataset_coverage

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    end_exclusive = end_d + timedelta(days=1) if end_d is not None else None
    window = (start_d, end_exclusive) if start_d is not None and end_exclusive is not None else None
    rows = []
    for row in audit_materialization():
        if source and row.name != source:
            continue
        materialization = None
        if source is not None:
            materialization = ensure_materialized(
                row.name,
                window=window,
                budget="manual",
            ).to_json()
        coverage = materialized_dataset_coverage(row, start=start_d, end=end_exclusive)
        payload = {
            "source": row.name,
            "status": row.status,
            "substrate_status": row.to_json()["substrate_status"],
            "collection_model": row.to_json()["collection_model"],
            "row_count": row.row_count,
            "first_date": _json_safe(row.first_date),
            "last_date": _json_safe(row.last_date),
            "coverage": coverage,
            "overlaps_requested_window": coverage["overlaps_requested_window"],
            "reason": row.reason,
            "materialization_hint": row.materialization_hint,
        }
        if row.to_json()["collection_model"] == "event_export":
            acquisition_gap = end_d is not None and (row.last_date is None or row.last_date < end_d)
            payload["freshness"] = {
                "source_event_last_date": _json_safe(row.last_date),
                "acquisition_gap": acquisition_gap,
                "needed_action": "acquire a newer owner export" if acquisition_gap else None,
                "interpretation": "reprocessing existing export bytes does not extend source event coverage",
            }
        if materialization is not None:
            payload["materialization"] = materialization
        rows.append(payload)
    return rows


def analysis_readiness(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """One-stop readiness for dataset contracts plus latest substrate statuses."""
    dataset_rows = contract_coverage(start=start, end=end)
    substrate = substrate_readiness_report()
    blocking_datasets = [
        row for row in dataset_rows
        if row["substrate_status"] in {"unavailable", "error"}
    ]
    continuous_coverage_gaps = [
        row for row in dataset_rows
        if row.get("collection_model") == "continuous"
        and isinstance(row.get("coverage"), dict)
        and row["coverage"].get("relation") in {"no_overlap", "partial_overlap"}
    ]
    blocking_stages = [
        row for row in substrate["sources"]
        if row.get("kind") == "stage" and row["status"] in {"unavailable", "error"}
    ]
    return {
        "requested_window": {"start": start, "end": end},
        "datasets": dataset_rows,
        "substrate": substrate,
        "summary": {
            "dataset_count": len(dataset_rows),
            "blocking_dataset_count": len(blocking_datasets),
            "continuous_coverage_gap_count": len(continuous_coverage_gaps),
            "blocking_stage_count": len(blocking_stages),
            "trustworthy": (
                not blocking_datasets
                and not continuous_coverage_gaps
                and not blocking_stages
                and substrate["summary"]["trustworthy"]
            ),
        },
        "blocking": {
            "datasets": blocking_datasets,
            "continuous_coverage_gaps": continuous_coverage_gaps,
            "stages": blocking_stages,
        },
    }


def promotion_runs(
    refresh_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Promotion run audit rows."""
    from lynchpin.substrate.connection import connect, substrate_path

    clauses = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(min(max(limit, 1), 1000))
    with connect(substrate_path(), read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT refresh_id, status, reason, window_start, window_end, mode,
                   counts, started_at, finished_at
            FROM substrate_promotion_run
            {where}
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        {
            "refresh_id": row[0],
            "status": row[1],
            "reason": row[2],
            "window_start": _json_safe(row[3]),
            "window_end": _json_safe(row[4]),
            "mode": row[5],
            "counts": row[6],
            "started_at": _json_safe(row[7]),
            "finished_at": _json_safe(row[8]),
        }
        for row in rows
    ]


def substrate_run_steps(
    refresh_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Durable progress rows for materialization and substrate promotion stages."""
    from lynchpin.substrate.connection import connect, substrate_path

    clauses = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(min(max(limit, 1), 1000))
    with connect(substrate_path(), read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT refresh_id, step, status, message, row_count,
                   started_at, finished_at, recorded_at
            FROM substrate_run_step
            {where}
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        {
            "refresh_id": row[0],
            "step": row[1],
            "status": row[2],
            "message": row[3],
            "row_count": row[4],
            "started_at": _json_safe(row[5]),
            "finished_at": _json_safe(row[6]),
            "recorded_at": _json_safe(row[7]),
        }
        for row in rows
    ]


def analysis_claims(
    refresh_id: str | None = None,
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    claim_type: str | None = None,
    min_confidence: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows, _selected_refresh_id, _publication_id = _read_analysis_claims(
        refresh_id=refresh_id,
        project=project,
        start=start,
        end=end,
        claim_type=claim_type,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    return rows


def _read_analysis_claims(
    *,
    refresh_id: str | None,
    project: str | None,
    start: str | None,
    end: str | None,
    claim_type: str | None,
    min_confidence: float | None,
    limit: int,
    offset: int,
    expected_publication_id: str | None = None,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Persisted analysis claims with confidence, caveats, and evidence IDs."""
    from datetime import date as _date

    from lynchpin.mcp.tools._utils import best_materialized_refresh_id
    from lynchpin.substrate.claims import load_analysis_claims
    from lynchpin.substrate.connection import serving_generation, substrate_path

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    if refresh_id is None:
        ensure_substrate_materialized_for_read(
            caller="analysis_claims",
            window=half_open_date_window(start_d, end_d),
        )

    with serving_generation(substrate_path()) as serving:
        if expected_publication_id is not None and serving.publication_id != expected_publication_id:
            raise QueryPublicationMismatch(
                expected_publication_id=expected_publication_id,
                actual_publication_id=serving.publication_id,
            )
        conn = serving.connection
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "analysis_claim",
                caller="analysis_claims",
            )
        if refresh_id is None:
            raise RuntimeError(
                "analysis_claims requires substrate table 'analysis_claim', "
                "but no promoted rows exist. Run materialization and inspect "
                "materialization_status / substrate_readiness_report."
            )
        rows = [
            _json_safe(row)
            for row in load_analysis_claims(
                conn,
                refresh_id=refresh_id,
                project=project,
                start=start_d,
                end=end_d,
                claim_type=claim_type,
                min_confidence=min_confidence,
                limit=limit,
                offset=offset,
            )
        ]
    selected_refresh_id = refresh_id or (rows[0].get("refresh_id") if rows else None)
    return rows, selected_refresh_id, serving.publication_id


def analysis_claims_page(
    refresh_id: str | None = None,
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    claim_type: str | None = None,
    min_confidence: float | None = None,
    limit: int = 200,
    offset: int = 0,
    expected_publication_id: str | None = None,
) -> dict[str, Any]:
    """Return one stable claim page, with enough metadata to continue it."""
    page_limit = min(max(int(limit), 1), 10_000)
    page_offset = int(offset)
    if page_offset < 0:
        raise ValueError("offset must be non-negative")

    rows, selected_refresh_id, publication_id = _read_analysis_claims(
        refresh_id=refresh_id,
        project=project,
        start=start,
        end=end,
        claim_type=claim_type,
        min_confidence=min_confidence,
        limit=page_limit + 1,
        offset=page_offset,
        expected_publication_id=expected_publication_id,
    )
    has_more = len(rows) > page_limit
    selected = rows[:page_limit]
    return {
        "rows": selected,
        "refresh_id": selected_refresh_id,
        "publication_id": publication_id,
        "limit": page_limit,
        "offset": page_offset,
        "has_more": has_more,
        "next_offset": page_offset + len(selected) if has_more else None,
    }


def claim_evidence(
    claim_id: str,
    refresh_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    expected_publication_id: str | None = None,
) -> dict[str, Any]:
    """Return one claim and integrity of its selected logical generation."""
    from lynchpin.substrate.claims import load_claim_evidence
    from lynchpin.mcp.tools._utils import best_materialized_refresh_id
    from lynchpin.substrate.connection import serving_generation, substrate_path

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="claim_evidence")

    with serving_generation(substrate_path()) as serving:
        if expected_publication_id is not None and serving.publication_id != expected_publication_id:
            raise QueryPublicationMismatch(
                expected_publication_id=expected_publication_id,
                actual_publication_id=serving.publication_id,
            )
        conn = serving.connection
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "analysis_claim",
                caller="claim_evidence",
            )
        if refresh_id is None:
            raise RuntimeError(
                "claim_evidence requires substrate table 'analysis_claim', "
                "but no promoted rows exist. Run materialization and inspect "
                "materialization_status / substrate_readiness_report."
            )
        row = load_claim_evidence(
            conn, claim_id=claim_id, refresh_id=refresh_id, limit=limit, offset=offset,
        )
        integrity = measure_graph_integrity(conn, refresh_id)
    result = _json_safe(row) if row is not None else {
        "summary": {"status": "missing"},
        "claim_id": claim_id,
        "refresh_id": refresh_id,
        "limit": limit,
        "offset": offset,
        "returned_count": 0,
        "truncated": False,
        "next_offset": None,
    }
    if row is not None:
        result["refresh_id"] = refresh_id
    result["publication_id"] = serving.publication_id
    result["graph_integrity"] = integrity
    result["caveats"] = list(result.get("caveats") or []) + [
        asdict(caveat) for caveat in integrity_caveats(integrity)
    ]
    return result


def analysis_claim_calibration(
    refresh_id: str | None = None,
    project: str | None = None,
    claim_type: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Internal-consistency audit for persisted analysis claims."""
    from lynchpin.analysis.claim_calibration import calibrate_claims

    rows = analysis_claims(
        refresh_id=refresh_id,
        project=project,
        claim_type=claim_type,
        limit=min(max(limit, 1), 10_000),
    )
    return _json_safe(calibrate_claims(rows).to_json())


def list_evidence_graph_builds(
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """List evidence-graph builds stored in the substrate.

    Wraps ``lynchpin.substrate.graph.list_evidence_graph_builds``.

    Parameters:
        start: ISO date string (YYYY-MM-DD) — filter by exact start_date.
        end:   ISO date string (YYYY-MM-DD) — filter by exact end_date.

    Returns:
        [
            {
                "refresh_id": str,
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "mode": str | None,
                "projects": [...],
                "node_count": int,
                "edge_count": int,
                "generated_at": "ISO datetime",
                "materialized_at": "ISO datetime | None",
            }
        ]
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import list_evidence_graph_builds as _list_builds

    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        rows = _list_builds(conn, start=start_d, end=end_d)
        return [
            {**_json_safe(row), "graph_integrity": measure_graph_integrity(conn, row["refresh_id"])}
            for row in rows
        ]


def load_evidence_graph_summary(
    refresh_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Hydrate an evidence graph build and return summary statistics.

    Does NOT return the full nodes/edges arrays — use ``query_substrate`` for
    that to control payload size.

    Parameters:
        refresh_id: exact build ID (takes precedence over start/end).
        start:      ISO date string — select most recent build for this start date.
        end:        ISO date string — select most recent build for this end date.

    Returns:
        {
            "build": {refresh_id, start_date, end_date, mode, generated_at},
            "node_kind_counts": {"commit": N, "ai_work_event": N, ...},
            "edge_relation_counts": {"file_overlap": N, ...},
            "project_day_summary": [
                {"project": str, "date_count": int, "total_commits": int}
            ],
        }

    Returns {"error": "no matching build"} when no build exists.
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import load_evidence_graph as _load_graph

    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None
    window = half_open_date_window(start_d, end_d)
    materialization = (
        ensure_substrate_materialized_for_read(
            caller="load_evidence_graph_summary",
            window=window,
        )
        if refresh_id is None
        else pinned_materialization_for_read(caller="load_evidence_graph_summary", refresh_id=refresh_id)
    )

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        graph = _load_graph(conn, refresh_id=refresh_id, start=start_d, end=end_d)
    integrity = (
        graph.graph_integrity if graph is not None and graph.graph_integrity is not None
        else unavailable_graph_integrity(refresh_id)
    )

    if graph is None:
        return {
            "error": "no matching build",
            "materialization": materialization,
            "graph_integrity": integrity,
            "caveats": [asdict(c) for c in integrity_caveats(integrity)],
        }

    node_kind_counts: dict[str, int] = {}
    for node in graph.nodes:
        node_kind_counts[node.kind] = node_kind_counts.get(node.kind, 0) + 1

    edge_relation_counts: dict[str, int] = {}
    for edge in graph.edges:
        edge_relation_counts[edge.relation] = (
            edge_relation_counts.get(edge.relation, 0) + 1
        )

    # project × date summary
    from collections import defaultdict

    proj_dates: dict[str, set[Any]] = defaultdict(set)
    proj_commits: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        if node.project and node.date:
            proj_dates[node.project].add(node.date)
            if node.kind == "commit":
                proj_commits[node.project] += 1

    project_day_summary = [
        {
            "project": proj,
            "date_count": len(dates),
            "total_commits": proj_commits.get(proj, 0),
        }
        for proj, dates in sorted(proj_dates.items())
    ]

    return {
        "materialization": materialization,
        "build": {
            "refresh_id": graph.refresh_id if hasattr(graph, "refresh_id") else refresh_id,
            "start_date": _json_safe(graph.start),
            "end_date": _json_safe(graph.end),
            "mode": graph.mode,
            "generated_at": _json_safe(graph.generated_at),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
        },
        "node_kind_counts": node_kind_counts,
        "edge_relation_counts": edge_relation_counts,
        "project_day_summary": project_day_summary,
        "graph_integrity": integrity,
        "caveats": [asdict(c) for c in graph.caveats],
    }


# ── M.13 AI-Attribution Backfill ─────────────────────────────────────────────


def ai_attribution_backfill(
    refresh_id: str | None = None,
    time_window_hours: int = 24,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill commit_fact.ai_attribution by matching commits to AI work events.

    Matches commits to ai_work_event rows where:
    - Same project
    - Commit authored_at within ±time_window_hours of work_event start_ts
    - Non-empty file_path intersection between commit.paths and
      ai_work_event.file_paths

    Writes a JSON object to commit_fact.ai_attribution:
    {
        "matched_events": N,
        "top_kinds": ["implementation", ...],
        "matched_via": "file_path_overlap",
        "backfilled_at": "ISO datetime"
    }

    This is an UPDATE — it modifies the substrate. Call with dry_run=True
    to preview without writing.

    Parameters:
        refresh_id:         snapshot to backfill; default = latest.
        time_window_hours:  ± hours window for temporal match (default 24).
        dry_run:            preview matches without writing.

    Returns:
        {
            "matched_commits": int,
            "total_commits": int,
            "match_rate": float,
            "dry_run": bool,
            "top_matches": [{"sha": str, "subject": str, "matched_events": int}],
        }
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from lynchpin.substrate.connection import candidate_generation, connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_commit_ai_join_refresh_id(conn)
            if refresh_id is None:
                return {"error": "no promote runs"}

        total = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()[0]

        # Match commits to AI work events by project + suffix-normalized path
        # overlap + time window. Commit paths are repo-relative while
        # Polylogue paths are often absolute or temporary-worktree paths, so
        # exact list intersection misses legitimate matches.
        matches = conn.execute("""
            WITH candidate_paths AS (
                SELECT
                    c.sha,
                    c.repo,
                    c.subject,
                    we.event_id,
                    we.kind,
                    cp.commit_path,
                    ep.event_path
                FROM commit_fact c
                JOIN ai_work_event we
                  ON c.refresh_id = we.refresh_id
                 AND c.project = we.project
                 AND ABS(EXTRACT(EPOCH FROM c.authored_at - we.start_ts)) < ?
                , UNNEST(c.paths) AS cp(commit_path)
                , UNNEST(we.file_paths) AS ep(event_path)
                WHERE we.start_ts IS NOT NULL
                  AND c.project IS NOT NULL
                  AND we.project IS NOT NULL
                  AND len(c.paths) > 0
                  AND len(we.file_paths) > 0
                  AND c.refresh_id = ?
            ),
            path_matches AS (
                SELECT DISTINCT sha, repo, subject, event_id, kind
                FROM candidate_paths
                WHERE
                    ends_with(ltrim(event_path, '/'), ltrim(commit_path, '/'))
                    OR ends_with(ltrim(commit_path, '/'), ltrim(event_path, '/'))
            )
            SELECT sha, repo, subject,
                   COUNT(event_id) AS matched_events,
                   ARRAY_AGG(DISTINCT kind) AS kinds,
                   ARRAY_AGG(DISTINCT event_id) AS event_ids
            FROM path_matches
            GROUP BY sha, repo, subject
            ORDER BY matched_events DESC
        """, [time_window_hours * 3600, refresh_id]).fetchall()

    if matches is None:
        matches = []

    matched_count = len(matches)
    now_iso = _dt.now(_tz.utc).isoformat()

    if not dry_run:
        with candidate_generation(changed_products=("commit_fact",)):
            with connect() as conn:
                for sha, repo, subject, cnt, kinds, event_ids in matches:
                    attribution = _json.dumps({
                        "matched_events": cnt,
                        "top_kinds": list(kinds[:5]) if kinds else [],
                        "matched_via": "project_suffix_path_overlap",
                        "backfilled_at": now_iso,
                    })
                    conn.execute(
                        "UPDATE commit_fact SET ai_attribution = ? "
                        "WHERE refresh_id = ? AND sha = ? AND repo = ?",
                        [attribution, refresh_id, sha, repo],
                    )

    return {
        "matched_commits": matched_count,
        "total_commits": total,
        "match_rate": round(matched_count / max(total, 1), 3),
        "dry_run": dry_run,
        "top_matches": [
            {"sha": r[0][:8], "subject": r[2][:60], "matched_events": r[3]}
            for r in (matches[:10] if matches else [])
        ],
    }


# ── Substrate Prune ──────────────────────────────────────────────────────────


def substrate_prune(
    keep_builds: int = 3,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prune old evidence graph builds to reclaim disk space.

    Deletes evidence_graph_build rows (and their nodes/edges) older than
    the most recent N builds. Keeps the latest `keep_builds` manual
    promotes and discards test/graph/overlap builds.

    Parameters:
        keep_builds: number of most recent builds to keep.
        dry_run:     preview without deleting (default True).

    Returns:
        {"builds_before": N, "builds_after": N, "nodes_deleted": N,
         "edges_deleted": N, "dry_run": bool}
    """
    from lynchpin.substrate.connection import candidate_generation, connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        builds_before = conn.execute(
            "SELECT COUNT(*) FROM evidence_graph_build"
        ).fetchone()[0]
        nodes_before = conn.execute(
            "SELECT COUNT(*) FROM evidence_node"
        ).fetchone()[0]
        edges_before = conn.execute(
            "SELECT COUNT(*) FROM evidence_edge"
        ).fetchone()[0]

        # Find refresh_ids to keep (latest N + manual promotes)
        manual = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT refresh_id FROM evidence_graph_build "
                "WHERE refresh_id NOT LIKE 'graph:%' "
                "AND refresh_id NOT LIKE 'overlap:%' "
                "ORDER BY generated_at DESC LIMIT ?",
                [keep_builds],
            ).fetchall()
        ]

        # Also keep the most recent test/graph build (for local development)
        latest_test = conn.execute(
            "SELECT refresh_id FROM evidence_graph_build "
            "WHERE refresh_id LIKE 'graph:%' "
            "ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if latest_test:
            manual.append(latest_test[0])

        to_delete = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT refresh_id FROM evidence_graph_build "
                "WHERE refresh_id NOT IN ({seq})".format(
                    seq=",".join(["?"] * len(manual))
                ),
                manual,
            ).fetchall()
        ] if manual else []

    if dry_run:
        return {
            "builds_before": builds_before,
            "builds_after": builds_before - len(to_delete),
            "nodes_before": nodes_before,
            "edges_before": edges_before,
            "builds_to_delete": len(to_delete),
            "dry_run": True,
        }

    with candidate_generation(changed_products=("evidence_graph_build", "evidence_node", "evidence_edge")):
        with connect() as conn:
            for rid in to_delete:
                conn.execute("DELETE FROM evidence_edge WHERE refresh_id = ?", [rid])
                conn.execute("DELETE FROM evidence_node WHERE refresh_id = ?", [rid])
                conn.execute("DELETE FROM evidence_graph_build WHERE refresh_id = ?", [rid])
            conn.execute("CHECKPOINT")
            builds_after = conn.execute(
                "SELECT COUNT(*) FROM evidence_graph_build"
            ).fetchone()[0]
            nodes_after = conn.execute(
                "SELECT COUNT(*) FROM evidence_node"
            ).fetchone()[0]
            edges_after = conn.execute(
                "SELECT COUNT(*) FROM evidence_edge"
            ).fetchone()[0]

    return {
        "builds_before": builds_before,
        "builds_after": builds_after,
        "builds_deleted": builds_before - builds_after,
        "nodes_deleted": nodes_before - nodes_after,
        "edges_deleted": edges_before - edges_after,
        "dry_run": False,
    }


def substrate_consistency_audit() -> dict[str, Any]:
    """Cross-check substrate_source_status.row_count against actual row counts.

    Status rows that record promotion outcomes can drift from reality when
    a promote-then-effect-disappears sequence occurs (e.g., a transaction
    rolled back silently after status was recorded, or another process
    touched the table). This tool walks every status row with status='ok',
    looks up the actual ``COUNT(*)`` for the same backing table set and
    refresh_id, and reports discrepancies.

    Returns:
        {
            "discrepancies": [
                {"source": str, "table": str, "tables": list[str], "refresh_id": str,
                 "claimed_rows": int, "actual_rows": int,
                 "diff": int},  # negative when actual < claimed (missing data)
                ...
            ],
            "checked_count": int,    # number of (source, refresh_id) pairs audited
            "trustworthy": bool,     # true iff zero discrepancies
        }

    Notes:
    - Sources whose status maps to multiple simple tables are checked by
      summing the rows across all mapped tables.
    - Graph products are skipped here; their integrity is covered by other reports.
    - A row_count mismatch isn't necessarily corruption — a later refresh
      could legitimately have promoted/replaced rows. But the status table
      should still match observable state, so any drift is worth surfacing.
    """
    from lynchpin.substrate.connection import connect, substrate_path

    from lynchpin.core.substrate_sources import SUBSTRATE_TABLE_SOURCE

    source_to_tables: dict[str, list[str]] = {}
    for table, source in SUBSTRATE_TABLE_SOURCE.items():
        source_to_tables.setdefault(source, []).append(table)
    # Skip graph products whose status row covers a logical product, not one
    # simple source-owned table set.
    skip_sources = {
        "evidence_graph",
        "evidence_graph_substrate",
    }

    with connect(substrate_path(), read_only=True) as conn:
        rows = conn.execute("""
            SELECT source, refresh_id, row_count
            FROM substrate_source_status
            WHERE status='ok' AND row_count IS NOT NULL
            ORDER BY recorded_at DESC
        """).fetchall()

        discrepancies: list[dict[str, Any]] = []
        checked = 0
        for source, refresh_id, claimed in rows:
            if source in skip_sources:
                continue
            tables = source_to_tables.get(source, [source])
            existing_tables: list[str] = []
            for table in tables:
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                    [table],
                ).fetchone()
                if exists:
                    existing_tables.append(table)
            if not existing_tables:
                continue
            actual = sum(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE refresh_id=?", [refresh_id],
                ).fetchone()[0]
                for table in existing_tables
            )
            checked += 1
            if actual != claimed:
                discrepancies.append({
                    "source": source,
                    "table": ",".join(existing_tables),
                    "tables": existing_tables,
                    "refresh_id": refresh_id,
                    "claimed_rows": claimed,
                    "actual_rows": actual,
                    "diff": actual - claimed,
                })

    return {
        "discrepancies": discrepancies,
        "checked_count": checked,
        "trustworthy": not discrepancies,
    }


# ── Consolidated Dispatchers ────────────────────────────────────────────────


def substrate_health(
    view: str = "readiness",
    refresh_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 50,
) -> Any:
    """Substrate health and promotion status.

    Parameters:
        view: readiness (overall readiness report), sources (per-source status),
              analysis (analysis readiness), runs (recent promotion run history),
              steps (promotion run step details), consistency (consistency audit).
    """
    if view == "readiness":
        return substrate_readiness_report()
    if view == "sources":
        return substrate_source_status(refresh_id=refresh_id, status=status, kind=kind)
    if view == "analysis":
        return analysis_readiness(start=start, end=end)
    if view == "runs":
        return promotion_runs(refresh_id=refresh_id, status=status, limit=limit)
    if view == "steps":
        return substrate_run_steps(refresh_id=refresh_id, status=status, limit=limit)
    if view == "consistency":
        return substrate_consistency_audit()
    return {"error": f"unknown view {view!r}. choices: readiness, sources, analysis, runs, steps, consistency"}


def evidence_graph(
    view: str = "builds",
    refresh_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> Any:
    """Evidence graph data.

    Parameters:
        view: builds (list recent evidence graph builds),
              summary (load evidence graph summary for a build).
    """
    if view == "builds":
        return list_evidence_graph_builds(start=start, end=end)
    if view == "summary":
        return load_evidence_graph_summary(refresh_id=refresh_id, start=start, end=end)
    return {"error": f"unknown view {view!r}. choices: builds, summary"}


def analysis_evidence(
    view: str = "claims",
    claim_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    claim_type: str | None = None,
    min_confidence: float | None = None,
    refresh_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    expected_publication_id: str | None = None,
) -> Any:
    """Analysis claims and evidence.

    Parameters:
        view: claims (one paged analysis-claims result for the date range),
              calibration (claim calibration statistics),
              evidence (evidence for a specific claim; requires claim_id).
    """
    if view == "claims":
        return analysis_claims_page(
            refresh_id=refresh_id,
            project=project,
            start=start,
            end=end,
            claim_type=claim_type,
            min_confidence=min_confidence,
            limit=limit,
            offset=offset,
            expected_publication_id=expected_publication_id,
        )
    if view == "calibration":
        return analysis_claim_calibration(refresh_id=refresh_id, project=project, claim_type=claim_type, limit=limit)
    if view == "evidence":
        if claim_id is None:
            return {"error": "claim_id is required for view=evidence"}
        return claim_evidence(
            claim_id=claim_id, refresh_id=refresh_id, limit=limit, offset=offset,
            expected_publication_id=expected_publication_id,
        )
    return {"error": f"unknown view {view!r}. choices: claims, calibration, evidence"}
