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
    conn.execute(f"DELETE FROM {table} WHERE refresh_id = ?", [refresh_id])

    if refresh_id_position == "first":
        column_list = ", ".join(("refresh_id", *columns))

        def build_tuple(extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (refresh_id, *extracted)
    else:
        column_list = ", ".join((*columns, "refresh_id"))

        def build_tuple(extracted: tuple[Any, ...]) -> tuple[Any, ...]:
            return (*extracted, refresh_id)

    placeholders = ", ".join(["?"] * (len(columns) + 1))
    insert_sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"

    total = 0
    buffer: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not buffer:
            return
        conn.executemany(insert_sql, buffer)
        total += len(buffer)
        buffer.clear()

    for row in rows:
        buffer.append(build_tuple(extractor(row)))
        if batch_size is not None and len(buffer) >= batch_size:
            flush()

    flush()
    log.debug("promote_rows: %d rows into %s for refresh_id=%s", total, table, refresh_id)
    return total
