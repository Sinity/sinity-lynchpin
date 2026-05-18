"""DuckDB substrate path management and lazy connection.

The substrate file lives at ``.lynchpin/duck/substrate.duckdb`` (under
``LynchpinConfig.generated_root`` so it sits alongside the JSON artifact dir,
not in the cachew cache).

Concurrency: single writer (refresh DAG); many readers via DuckDB's MVCC.
We never run concurrent writers today; the assumption is documented here so
future MCP-server work knows the constraint.

Schema versioning: we track ``SUBSTRATE_VERSION``. When it changes, the
``apply_schema`` step drops + recreates rather than migrating — the substrate
is *derived* from sources, not authoritative. Re-promote is cheap.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import duckdb

SUBSTRATE_VERSION = 16
"""Bump on schema-incompatible changes; triggers drop-and-rebuild on next promote."""


def substrate_path() -> Path:
    """Return the canonical DuckDB substrate file path."""
    from lynchpin.core.config import get_config

    cfg = get_config()
    duck_dir = cfg.local_root / "duck"
    duck_dir.mkdir(parents=True, exist_ok=True)
    return duck_dir / "substrate.duckdb"


@contextmanager
def connect(path: Path | None = None, *, read_only: bool = False) -> Iterator["duckdb.DuckDBPyConnection"]:
    """Yield a DuckDB connection to the substrate.

    Caller responsibility: do not run concurrent writers. Reads are MVCC-safe.
    """
    import duckdb

    target = path if path is not None else substrate_path()
    conn = duckdb.connect(str(target), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def reset_substrate(path: Path | None = None) -> None:
    """Delete the substrate file. Used by tests and on schema-version bump."""
    target = path if path is not None else substrate_path()
    if target.exists():
        target.unlink()


def apply_schema(conn: "duckdb.DuckDBPyConnection") -> None:
    """Apply the substrate DDL idempotently.

    Reads ``SUBSTRATE_VERSION`` from a ``substrate_meta`` table. If absent
    or stale, drops all tables and re-applies the full DDL. Otherwise no-op.
    """
    from lynchpin.substrate.schema import DDL_STATEMENTS, DROP_STATEMENTS
    from lynchpin.substrate.views import ensure_views

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS substrate_meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
        """
    )
    row = conn.execute(
        "SELECT value FROM substrate_meta WHERE key = 'version'"
    ).fetchone()
    current = int(row[0]) if row else None

    if current != SUBSTRATE_VERSION:
        for stmt in DROP_STATEMENTS:
            conn.execute(stmt)
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO substrate_meta VALUES ('version', ?)",
            [str(SUBSTRATE_VERSION)],
        )
    ensure_views(conn)
