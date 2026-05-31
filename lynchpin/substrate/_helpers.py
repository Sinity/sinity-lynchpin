"""Shared substrate-promoter helper.

15 ``promote_*`` functions across the substrate layer each repeat the
same body shape: DELETE rows for the current refresh_id, build a list
of tuples from a typed dataclass iterable, executemany INSERT. The
only varying parts are the table name, column list, and the per-row
extraction function.

:func:`promote_rows` consolidates that boilerplate into a single
helper. Per-source promoters become a thin wrapper that supplies the
extractor lambda — typically 8–12 lines instead of 30–50.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, TypeVar

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

T = TypeVar("T")

RefreshIdPosition = Literal["last", "first"]


def promote_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    table: str,
    columns: tuple[str, ...],
    refresh_id: str,
    rows: Iterable[T],
    extractor: Callable[[T], tuple[Any, ...]],
    batch_size: int | None = None,
    refresh_id_position: RefreshIdPosition = "last",
    delete_existing: bool = True,
    wrap_transaction: bool = True,
) -> int:
    """INSERT rows into ``table``, idempotent on refresh_id.

    ``extractor`` receives one source-domain object at a time and must
    return a tuple whose length matches ``columns``. The function
    injects ``refresh_id`` automatically — the column list and tuple
    SHOULD NOT include it.

    DELETE-then-INSERT is the existing substrate idempotence contract
    (re-running a refresh with the same refresh_id replaces rather
    than duplicates).

    ``batch_size`` (optional): when set, accumulate at most N tuples
    in memory before flushing with executemany, then continue. Used
    by machine.py's high-volume sample promoters (50K rows per flush)
    to keep peak memory bounded. ``None`` means single flush.

    ``refresh_id_position`` (optional): ``"last"`` (default) appends
    refresh_id to the end of every row + the column list. ``"first"``
    prepends instead — required by evidence_node / evidence_edge whose
    schema places refresh_id as column 1.

    Returns the number of rows inserted (zero if ``rows`` was empty).
    """
    if refresh_id_position == "first":
        ordered_columns = ("refresh_id", *columns)

        def build_tuple(extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (refresh_id, *extracted)
    else:
        ordered_columns = (*columns, "refresh_id")

        def build_tuple(extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (*extracted, refresh_id)

    column_list = ", ".join(ordered_columns)
    placeholders = ", ".join(["?"] * (len(columns) + 1))
    insert_sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"

    try:
        import pandas as _pd
    except ImportError:  # pragma: no cover - pandas ships with the analytics env
        _pd = None

    total = 0
    buffer: list[tuple[Any, ...]] = []
    df_token = "_promote_rows_df"

    def flush() -> None:
        nonlocal total
        if not buffer:
            return
        # Fast path: DuckDB ingests a pandas DataFrame vectorized (~383x faster
        # than row-by-row executemany on a nullable-BIGINT A/B, byte-identical
        # output via convert_dtypes which preserves nullable ints). Only usable
        # when no value is an actual STRUCT/LIST container (dict/list/tuple/set);
        # JSON columns hold strings and are fine. Fall back to executemany for
        # container values or when pandas is unavailable.
        if _pd is not None and not any(
            isinstance(v, (dict, list, tuple, set, frozenset))
            for r in buffer for v in r
        ):
            frame = _pd.DataFrame(buffer, columns=list(ordered_columns)).convert_dtypes()
            conn.register(df_token, frame)
            try:
                conn.execute(
                    f"INSERT INTO {table} ({column_list}) SELECT * FROM {df_token}"
                )
            finally:
                conn.unregister(df_token)
        else:
            conn.executemany(insert_sql, buffer)
        total += len(buffer)
        buffer.clear()

    # Wrap DELETE + every INSERT batch in ONE transaction. In DuckDB autocommit,
    # executemany commits (and fsyncs the WAL) per row — measured at ~120KB-1.8MB
    # written/row and ~99% of substrate-promote wall-time (the artifacts promote
    # wrote 20GB / took 14min for ~163k rows). A single explicit transaction
    # collapses that to one commit: commit_fact A/B showed 85.5s/7.6GB -> 2.6s/29MB
    # (33x faster, 260x fewer bytes). Callers that already manage their own
    # transaction (e.g. the evidence-graph promote) pass wrap_transaction=False;
    # we cannot probe-by-BEGIN because a nested BEGIN error aborts the caller's
    # open transaction.
    #
    # The DELETE stays in autocommit, BEFORE the INSERT transaction: DuckDB's
    # primary-key index does not reflect an in-transaction DELETE, so a
    # DELETE-then-INSERT of the same refresh_id within one transaction trips a
    # phantom duplicate-key constraint (the same limitation evidence-graph works
    # around). Committing the DELETE first keeps idempotent re-promotion correct;
    # only the row-by-row INSERT churn needed batching.
    if delete_existing:
        conn.execute(f"DELETE FROM {table} WHERE refresh_id = ?", [refresh_id])

    if wrap_transaction:
        conn.execute("BEGIN TRANSACTION")
    try:
        for row in rows:
            buffer.append(build_tuple(extractor(row)))
            if batch_size is not None and len(buffer) >= batch_size:
                flush()
        flush()
        if wrap_transaction:
            conn.execute("COMMIT")
    except Exception:
        if wrap_transaction:
            conn.execute("ROLLBACK")
        raise

    log.debug("promote_rows: %d rows into %s for refresh_id=%s", total, table, refresh_id)
    return total
