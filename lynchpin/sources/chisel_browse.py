#!/usr/bin/env python3
"""Offline reader copied verbatim into each Chisel package; stdlib only."""

from __future__ import annotations
import argparse
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def connect(package: Path) -> sqlite3.Connection:
    db = (package / "index.sqlite3").resolve()
    if package.resolve() not in db.parents:
        raise ValueError("index path escapes package")
    uri = "file:" + quote(str(db), safe="/") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def search(package: Path, query: str) -> int:
    with connect(package) as db:
        has_fts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='package_fts'"
        ).fetchone()
        rows = []
        if has_fts:
            try:
                rows = db.execute(
                    "SELECT kind, reference, snippet(package_fts,2,'[',']','…',12) AS excerpt "
                    "FROM package_fts WHERE package_fts MATCH ? LIMIT 100",
                    (query,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            needle = query.casefold()
            for row in db.execute(
                "SELECT 'source' AS kind, path AS reference, content FROM source_files WHERE content IS NOT NULL "
                "UNION ALL SELECT 'record' AS kind, dataset || ':' || line AS reference, record AS content FROM datasets"
            ):
                idx = row["content"].casefold().find(needle)
                if idx >= 0:
                    excerpt = row["content"][
                        max(0, idx - 100) : idx + len(query) + 100
                    ].replace("\n", " ")
                    rows.append(
                        {
                            "kind": row["kind"],
                            "reference": row["reference"],
                            "excerpt": excerpt,
                        }
                    )
                    if len(rows) >= 100:
                        break
        for row in rows:
            print(f"{row['kind']} {row['reference']}: {row['excerpt']}")
        return 0 if rows else 1


def source(package: Path, path: str, start: int, end: int) -> int:
    rel = Path(path.removeprefix("source/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("source path must stay inside source/")
    target = (package / "source" / rel).resolve()
    root = (package / "source").resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError("captured source file not found")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if start < 1 or end < start:
        raise ValueError("line range must be positive and ordered")
    for n in range(start, min(end, len(lines)) + 1):
        print(f"{n:6}: {lines[n-1]}")
    return 0


def history(package: Path, path: str | None, commit: str | None) -> int:
    with connect(package) as db:
        rows = db.execute(
            "SELECT dataset,line,record FROM datasets WHERE dataset LIKE 'history/%' ORDER BY dataset,line"
        )
        found = 0
        for row in rows:
            record = row["record"]
            if path and path.casefold() not in record.casefold():
                continue
            if commit and commit.casefold() not in record.casefold():
                continue
            print(f"{row['dataset']}:{row['line']} {record}")
            found += 1
        return 0 if found else 1


def sql(package: Path, query: str) -> int:
    if not re.match(r"\s*(SELECT|WITH)\b", query, re.I):
        raise ValueError("only SELECT or WITH queries are allowed")
    with connect(package) as db:
        cur = db.execute(query)
        if cur.description:
            print("\t".join(c[0] for c in cur.description))
            for row in cur:
                print("\t".join("" if v is None else str(v) for v in row))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("search")
    p.add_argument("query")
    p = sub.add_parser("source")
    p.add_argument("path")
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int, default=80)
    p = sub.add_parser("history")
    p.add_argument("--path")
    p.add_argument("--commit")
    p = sub.add_parser("sql")
    p.add_argument("query")
    args = parser.parse_args()
    try:
        if args.command == "search":
            return search(args.package, args.query)
        if args.command == "source":
            return source(args.package, args.path, args.start, args.end)
        if args.command == "history":
            return history(args.package, args.path, args.commit)
        return sql(args.package, args.query)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
