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
from typing import TYPE_CHECKING, Any, Callable, Iterable, TypeVar

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

T = TypeVar("T")


def promote_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    table: str,
    columns: tuple[str, ...],
    refresh_id: str,
    rows: Iterable[T],
    extractor: Callable[[T], tuple[Any, ...]],
) -> int:
    """INSERT rows into ``table``, idempotent on refresh_id.

    ``extractor`` receives one source-domain object at a time and must
    return a tuple whose length matches ``columns``. The function
    appends ``refresh_id`` automatically as the last column — the
    column list and tuple SHOULD NOT include it.

    DELETE-then-INSERT is the existing substrate idempotence contract
    (re-running a refresh with the same refresh_id replaces rather
    than duplicates), retained here for consistency with the 15
    handwritten promoters this helper subsumes.

    Returns the number of rows inserted (zero if ``rows`` was empty).
    """
    conn.execute(f"DELETE FROM {table} WHERE refresh_id = ?", [refresh_id])

    tuples: list[tuple[Any, ...]] = []
    for row in rows:
        extracted = extractor(row)
        tuples.append((*extracted, refresh_id))

    if tuples:
        placeholders = ", ".join(["?"] * (len(columns) + 1))
        column_list = ", ".join((*columns, "refresh_id"))
        conn.executemany(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            tuples,
        )
    log.debug("promote_rows: %d rows into %s for refresh_id=%s", len(tuples), table, refresh_id)
    return len(tuples)
