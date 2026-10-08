"""Relocate current snapshot lookup addresses without rebuilding captured packages."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def location_changes(conn: Any, moves: list[dict[str, str]]) -> list[dict[str, str]]:
    """Plan only current derived lookup columns; all capture fields stay intact."""
    ordered = sorted(moves, key=lambda row: len(row["source"]), reverse=True)
    for move in ordered:
        source, destination = Path(move["source"]), Path(move["destination"])
        if not source.is_absolute() or not destination.is_absolute() or ".." in (*source.parts, *destination.parts):
            raise ValueError("snapshot locations must be absolute normalized paths")
        if source == destination:
            raise ValueError("snapshot location must change")
    changes = []
    for table, column in (("code_snapshot_run", "output_dir"), ("code_snapshot_slice", "path")):
        for project, old in conn.execute(f"SELECT project, {column} FROM {table} WHERE refresh_id = 'latest'").fetchall():
            if not old:
                continue
            for move in ordered:
                prefix = move["source"]
                if old == prefix or old.startswith(prefix + "/"):
                    new = move["destination"] + old[len(prefix):]
                    changes.append({"table": table, "column": column, "project": project,
                                    "before": old, "after": new})
                    break
    return changes


def apply_location_changes(conn: Any, changes: list[dict[str, str]]) -> None:
    """Update validated lookup addresses in the caller's candidate transaction."""
    for change in changes:
        table, column = change["table"], change["column"]
        if (table, column) not in {("code_snapshot_run", "output_dir"), ("code_snapshot_slice", "path")}:
            raise ValueError("only snapshot lookup addresses may change")
    conn.execute("BEGIN TRANSACTION")
    try:
        for change in changes:
            conn.execute(f"UPDATE {change['table']} SET {change['column']} = ? "
                         f"WHERE refresh_id = 'latest' AND project = ? AND {change['column']} = ?",
                         [change["after"], change["project"], change["before"]])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def main() -> None:
    from lynchpin.substrate.connection import candidate_generation, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moves", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--local-root", type=Path, help="Explicit owner store when running from an isolated checkout")
    args = parser.parse_args()
    if args.local_root is not None:
        os.environ["LYNCHPIN_LOCAL_ROOT"] = str(args.local_root.resolve())
    moves = json.loads(args.moves.read_text())
    with connect(read_only=True) as conn:
        changes = location_changes(conn, moves)
    if args.apply and changes:
        with candidate_generation(changed_products=("code_snapshot_locations",)):
            with connect() as conn:
                current = location_changes(conn, moves)
                if current != changes:
                    raise RuntimeError("snapshot lookups changed since planning")
                apply_location_changes(conn, changes)
    print(json.dumps({"applied": args.apply, "changes": changes}, indent=2))


if __name__ == "__main__":
    main()
