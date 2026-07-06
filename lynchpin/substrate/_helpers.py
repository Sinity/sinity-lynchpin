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

import gc
import logging
import uuid
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, TypeVar

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

T = TypeVar("T")

RefreshIdPosition = Literal["last", "first"]

_STAGING_PREFIX = "\x00promote_rows_staging\x00"


def _staging_refresh_id(refresh_id: str) -> str:
    """A refresh_id value guaranteed not to collide with any real refresh_id.

    The NUL-delimited prefix keeps it out of the human-facing refresh_id
    vocabulary (real ids are colon-joined slugs), and the per-call UUID makes
    it unique even across concurrent/retried promotions of the same target.
    """
    return f"{_STAGING_PREFIX}{refresh_id}\x00{uuid.uuid4().hex}"


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

    **Interruption safety** (``delete_existing=True`` only): rows are first
    inserted under a private staging refresh_id that cannot collide with any
    real refresh_id, inside one transaction. Only after that transaction
    commits does the function delete the target refresh_id's old rows and
    rename the staged rows onto it. If the process dies (SIGKILL — e.g. an
    OOM kill) or raises anywhere during row generation/insertion, the target
    refresh_id's previously-promoted rows are completely untouched — a
    generator/executemany failure can no longer silently delete good data and
    then fail to replace it (see ``docs/`` history: this was an observed
    real-world data-loss mode on machine telemetry tables, where the daily
    promotion job intermittently died mid-run under host memory pressure).

    Returns the number of rows inserted (zero if ``rows`` was empty).
    """
    if refresh_id_position == "first":
        ordered_columns = ("refresh_id", *columns)

        def build_tuple(write_id: str, extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (write_id, *extracted)
    else:
        ordered_columns = (*columns, "refresh_id")

        def build_tuple(write_id: str, extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (*extracted, write_id)

    # When delete_existing, write under a staging id first and swap it onto
    # the real refresh_id only after a full successful commit (see docstring).
    # When not delete_existing (append-only callers that manage their own
    # dedup, e.g. evidence-graph), write directly under refresh_id as before.
    write_id = _staging_refresh_id(refresh_id) if delete_existing else refresh_id

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
        if batch_size is not None:
            gc.collect()

    # Wrap every INSERT batch in ONE transaction. In DuckDB autocommit,
    # executemany commits (and fsyncs the WAL) per row — measured at ~120KB-1.8MB
    # written/row and ~99% of substrate-promote wall-time (the artifacts promote
    # wrote 20GB / took 14min for ~163k rows). A single explicit transaction
    # collapses that to one commit: commit_fact A/B showed 85.5s/7.6GB -> 2.6s/29MB
    # (33x faster, 260x fewer bytes). Callers that already manage their own
    # transaction (e.g. the evidence-graph promote) pass wrap_transaction=False;
    # we cannot probe-by-BEGIN because a nested BEGIN error aborts the caller's
    # open transaction.
    if wrap_transaction:
        conn.execute("BEGIN TRANSACTION")
    try:
        for row in rows:
            buffer.append(build_tuple(write_id, extractor(row)))
            if batch_size is not None and len(buffer) >= batch_size:
                flush()
        flush()
        if wrap_transaction:
            conn.execute("COMMIT")
    except Exception:
        if wrap_transaction:
            conn.execute("ROLLBACK")
        raise

    if delete_existing:
        # Swap the fully-committed staged rows onto the real refresh_id as two
        # fast, separate autocommit statements (deliberately NOT one
        # transaction): DuckDB's primary-key index does not reflect an
        # in-transaction DELETE, so a DELETE-then-INSERT/UPDATE of the same
        # key within one transaction trips a phantom duplicate-key constraint
        # (the same limitation evidence-graph works around by disabling
        # delete_existing entirely). Running the DELETE to completion first,
        # as its own committed statement, means the UPDATE's key check sees a
        # world that already lacks the old refresh_id rows. This shrinks the
        # "old data already gone, new data not yet in" window from the entire
        # row-generation + insert pass down to two near-instant metadata
        # operations.
        conn.execute(f"DELETE FROM {table} WHERE refresh_id = ?", [refresh_id])
        conn.execute(
            f"UPDATE {table} SET refresh_id = ? WHERE refresh_id = ?",
            [refresh_id, write_id],
        )

    log.debug("promote_rows: %d rows into %s for refresh_id=%s", total, table, refresh_id)
    return total
