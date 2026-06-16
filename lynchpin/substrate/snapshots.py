"""Materialized substrate snapshot selection helpers."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    return row is not None


def _table_row_count(conn: Any, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def latest_materialized_snapshot(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> tuple[str, Any] | None:
    """Return the latest successful substrate snapshot id and timestamp.

    ``substrate_promotion_run`` is the snapshot boundary. Individual
    ``substrate_source_status`` rows are component observations, so a later
    narrow source status should not replace the latest successful promotion run
    in broad readiness/runtime reports. The status-table fallback keeps older
    minimal substrates and tests readable.
    """

    _ = caller, ledger_path
    try:
        has_promotion_run = _table_exists(conn, "substrate_promotion_run")
        promotion_run_count = (
            _table_row_count(conn, "substrate_promotion_run") if has_promotion_run else 0
        )
    except Exception:
        has_promotion_run = False
        promotion_run_count = 0
    if has_promotion_run and promotion_run_count > 0:
        row = conn.execute(
            "SELECT refresh_id, finished_at FROM substrate_promotion_run "
            "WHERE status = 'ok' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return (str(row[0]), row[1]) if row else None

    try:
        row = conn.execute(
            "SELECT refresh_id, MAX(recorded_at) AS recorded_at "
            "FROM substrate_source_status "
            "GROUP BY refresh_id "
            "ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    return (str(row[0]), row[1]) if row else None


def latest_promotion_snapshot(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> tuple[str, Any, str, str | None] | None:
    """Return the latest recorded promotion attempt, regardless of outcome.

    Successful promotion runs remain the canonical materialized snapshot
    boundary. This helper is for status/readiness surfaces that must explain a
    partially populated substrate after a DAG failed late.
    """

    _ = caller, ledger_path
    try:
        has_promotion_run = _table_exists(conn, "substrate_promotion_run")
        promotion_run_count = (
            _table_row_count(conn, "substrate_promotion_run") if has_promotion_run else 0
        )
    except Exception:
        has_promotion_run = False
        promotion_run_count = 0
    if has_promotion_run and promotion_run_count > 0:
        row = conn.execute(
            "SELECT refresh_id, finished_at, status, reason "
            "FROM substrate_promotion_run "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return (str(row[0]), row[1], str(row[2]), row[3]) if row else None

    try:
        row = conn.execute(
            "SELECT refresh_id, MAX(recorded_at) AS recorded_at "
            "FROM substrate_source_status "
            "GROUP BY refresh_id "
            "ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    return (str(row[0]), row[1], "ok", None) if row else None


def ordered_materialized_refresh_ids(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> list[str]:
    """Return materialized substrate snapshot IDs ordered oldest to newest."""

    _ = caller, ledger_path
    try:
        has_promotion_run = _table_exists(conn, "substrate_promotion_run")
        promotion_run_count = (
            _table_row_count(conn, "substrate_promotion_run") if has_promotion_run else 0
        )
    except Exception:
        has_promotion_run = False
        promotion_run_count = 0
    if has_promotion_run and promotion_run_count > 0:
        rows = conn.execute(
            "SELECT refresh_id FROM substrate_promotion_run "
            "WHERE status = 'ok' "
            "ORDER BY finished_at"
        ).fetchall()
        return [str(row[0]) for row in rows]

    try:
        rows = conn.execute(
            "SELECT refresh_id, MAX(recorded_at) AS recorded_at "
            "FROM substrate_source_status "
            "GROUP BY refresh_id "
            "ORDER BY recorded_at"
        ).fetchall()
    except Exception:
        return []
    return [str(row[0]) for row in rows]


def latest_materialized_refresh_id(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the latest materialized substrate refresh_id."""

    row = latest_materialized_snapshot(conn, caller=caller, ledger_path=ledger_path)
    return row[0] if row else None



def best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the highest-coverage materialized refresh_id for a table.

    Multiple materialization scopes populate the same fact tables. A recent
    current-state materialization can be narrower than an older DAG
    materialization, so ordinary read defaults prefer table coverage first and
    recency second.
    """

    _ = caller, ledger_path
    if not _IDENTIFIER_RE.fullmatch(table):
        raise ValueError(f"invalid substrate table identifier: {table!r}")

    from lynchpin.core.substrate_sources import source_for_substrate_table

    source_name = source_for_substrate_table(table)
    order_expr = _fallback_order_expr(conn, table)
    source_status_known = False

    try:
        status_row = conn.execute(
            "SELECT COUNT(*) FROM substrate_source_status WHERE source = ?",
            [source_name],
        ).fetchone()
        source_status_known = bool(status_row and status_row[0])
        candidates = conn.execute(
            "SELECT refresh_id, recorded_at FROM substrate_source_status "
            "WHERE source = ? AND status = 'ok' "
            "ORDER BY recorded_at DESC",
            [source_name],
        ).fetchall()
        if candidates:
            ids = [row[0] for row in candidates]
            recorded_at_by_id = {row[0]: row[1] for row in candidates}
            placeholders = ",".join("?" * len(ids))
            ranked = conn.execute(
                f"SELECT refresh_id, COUNT(*) AS rc FROM {table} "
                f"WHERE refresh_id IN ({placeholders}) "
                "GROUP BY refresh_id",
                ids,
            ).fetchall()
            if ranked:
                ranked.sort(
                    key=lambda row: (row[1], recorded_at_by_id.get(row[0])),
                    reverse=True,
                )
                return str(ranked[0][0])
    except Exception:
        pass
    if source_status_known:
        logging.getLogger(__name__).warning(
            f"best_materialized_refresh_id({table!r}): source_status rows exist for "
            f"{source_name!r}, but none point to promoted rows in {table!r}"
        )
        return None

    row = conn.execute(
        f"SELECT refresh_id, COUNT(*) AS rc FROM {table} "
        f"GROUP BY refresh_id ORDER BY rc DESC, {order_expr} DESC LIMIT 1"
    ).fetchone()

    if row:
        logging.getLogger(__name__).warning(
            f"best_materialized_refresh_id({table!r}): no refresh_id with source_status "
            f"'{source_name}:ok' found; using highest-coverage materialization "
            f"{row[0]!r} (row_count={row[1]})"
        )
    return str(row[0]) if row else None


def require_best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    tool: str,
    ledger_path: Path | None = None,
) -> str:
    """Return the best materialized refresh id or raise when no data exists."""

    refresh_id = best_materialized_refresh_id(
        conn,
        table,
        caller=caller,
        ledger_path=ledger_path,
    )
    if refresh_id is None:
        raise RuntimeError(
            f"{tool} requires substrate table {table!r}, but no promoted rows exist. "
            "Run `python -m lynchpin.cli.materialize --all --promote --start YYYY-MM-DD --end YYYY-MM-DD` "
            "and inspect `materialization_status` / `substrate_readiness_report`."
        )
    return refresh_id


def _fallback_order_expr(conn: Any, table: str) -> str:
    columns = {str(row[0]) for row in conn.execute(f"DESCRIBE {table}").fetchall()}
    if "materialized_at" in columns:
        return "MAX(materialized_at)"
    if "recorded_at" in columns:
        return "MAX(recorded_at)"
    if "date" in columns:
        return "MAX(date), COUNT(*)"
    return "refresh_id"
