"""Materializer for the polylogue_verify_runs substrate product.

Reads Polylogue's durable cross-worktree verification history and promotes one
row per invocation. The history is append-only and already worktree-safe, so
this materializer is a pure read-and-promote: no side effects on the source.
"""

from __future__ import annotations

from typing import Any

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest._manifest import write_manifest

POLYLOGUE_VERIFY_SCHEMA_VERSION = 1


def materialize_polylogue_verify_runs() -> dict[str, Any]:
    """Promote every recorded Polylogue verification run into the substrate."""
    from lynchpin.core.config import get_config
    from lynchpin.sources.polylogue_verify import iter_verify_runs, verify_history_path
    from lynchpin.substrate.connection import connect, update_read_snapshot
    from lynchpin.substrate.polylogue_verify import promote_polylogue_verify_runs
    from lynchpin.substrate.schema import polylogue_verify_ddl

    history = verify_history_path()
    if not history.is_file():
        raise MaterializationError(
            "polylogue_verify_runs",
            reason=f"no Polylogue verification history at {history}",
        )

    rows = list(iter_verify_runs(history))
    with connect(rebuild_corrupt=True) as conn:
        # Additive table: create it in place rather than forcing a
        # SUBSTRATE_VERSION bump, which drops and rebuilds every product.
        for statement in polylogue_verify_ddl():
            conn.execute(statement)
        promoted = promote_polylogue_verify_runs(conn, rows=rows)

    update_read_snapshot()
    checkouts = sorted(
        {row["checkout_name"] for row in rows if row.get("checkout_name")}
    )
    manifest = {
        "dataset": "polylogue_verify_runs",
        "schema_version": POLYLOGUE_VERIFY_SCHEMA_VERSION,
        "row_count": promoted,
        "source_path": str(history),
        "checkout_count": len(checkouts),
        "checkouts": checkouts,
    }
    manifest_path = (
        get_config().derived_root
        / "polylogue_verify/polylogue_verify_runs.manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest_path, manifest)
    return manifest
