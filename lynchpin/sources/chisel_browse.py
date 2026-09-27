#!/usr/bin/env python3
"""Offline reader copied verbatim into each Chisel package; stdlib only."""

from __future__ import annotations
import argparse
import json
import hashlib
import shutil
import re
import sys
from pathlib import Path
from urllib.parse import quote

try:
    import sqlite3
except ImportError:
    sqlite3 = None


def connect(package: Path) -> sqlite3.Connection:
    if sqlite3 is None:
        raise ValueError("SQLite is unavailable; use source and JSONL queries")
    db = (package / "index.sqlite3").resolve()
    if not db.is_file():
        raise ValueError("SQL index is available in the full local package; this attachment keeps JSONL and source files")
    if package.resolve() not in db.parents:
        raise ValueError("index path escapes package")
    uri = "file:" + quote(str(db), safe="/") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def evidence_rows(package: Path):
    roots = [package / "inventory.jsonl"]
    for name in ("history", "structure", "trackers", "verification", "metrics", "reports", "context"):
        area = package / name
        if area.is_dir():
            roots.extend(sorted(area.rglob("*.jsonl")))
            roots.extend(sorted(area.rglob("*.ndjson")))
    for path in roots:
        if path.is_file():
            reference = path.relative_to(package).as_posix()
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line_no, line in enumerate(stream, 1):
                    yield reference, line_no, line.rstrip("\n")


def search(package: Path, query: str) -> int:
    if not (package / "index.sqlite3").is_file():
        needle = query.casefold()
        found = 0
        source_root = package / "source"
        if source_root.is_dir():
            for path in sorted(p for p in source_root.rglob("*") if p.is_file()):
                try:
                    content = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                offset = content.casefold().find(needle)
                if offset >= 0:
                    excerpt = content[max(0, offset - 100):offset + len(query) + 100].replace("\n", " ")
                    print(f"source {path.relative_to(package)}: {excerpt}")
                    found += 1
                    if found >= 100:
                        return 0
        for reference, line_no, content in evidence_rows(package):
            offset = content.casefold().find(needle)
            if offset >= 0:
                excerpt = content[max(0, offset - 100):offset + len(query) + 100]
                print(f"record {reference}:{line_no}: {excerpt}")
                found += 1
                if found >= 100:
                    return 0
        return 0 if found else 1
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


def source(package: Path, path: str, start: int, end: int, snapshot: str = "primary") -> int:
    rel = Path(path.removeprefix("source/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("source path must stay inside source/")
    target = (package / "source" / rel).resolve()
    root = (package / "source").resolve()
    if snapshot != "primary":
        if "/" in snapshot or ".." in snapshot or "\\" in snapshot:
            raise ValueError("invalid snapshot name")
        overlay = package / "snapshots" / snapshot
        manifest = json.loads((overlay / "manifest.json").read_text())
        if rel.as_posix() in manifest["deleted"]:
            raise ValueError("file deleted in selected snapshot")
        if rel.as_posix() in manifest["changed"]:
            root = (overlay / "files").resolve()
            target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError("captured source file not found")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if start < 1 or end < start:
        raise ValueError("line range must be positive and ordered")
    for n in range(start, min(end, len(lines)) + 1):
        print(f"{n:6}: {lines[n-1]}")
    return 0


def snapshot_identity(package: Path, name: str) -> str | None:
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise ValueError("invalid snapshot name")
    catalogue = package / "snapshots.json"
    if catalogue.is_file():
        for row in json.loads(catalogue.read_text()).get("snapshots", []):
            if row.get("name") == name:
                identity = row.get("snapshot_id")
                if not identity:
                    raise ValueError(f"selected snapshot is unavailable: {name}")
                return identity
        raise ValueError(f"unknown snapshot: {name}")
    if name != "primary":
        raise ValueError(f"unknown snapshot: {name}")
    # Older packages have no selectable catalogue or row-level snapshot IDs.
    return None


def query_records(package: Path, command: str, value: str | None, limit: int, offset: int, snapshot: str = "primary") -> dict:
    if limit < 1 or limit > 1000 or offset < 0:
        raise ValueError("limit must be 1..1000; offset must be nonnegative")
    if command == "snapshots":
        path = package / "snapshots.json"
        result = json.loads(path.read_text())["snapshots"] if path.exists() else [json.loads((package / "capture.json").read_text())]
    elif command == "blockers":
        if snapshot != "primary":
            raise ValueError("task blockers use an owner snapshot, not a source snapshot")
        graph = json.loads((package / "reports/task-dependencies.json").read_text())
        edges = graph["edges"]
        pending = [value]
        seen = set()
        result = []
        while pending:
            key = pending.pop(0)
            if key in seen:
                continue
            seen.add(key)
            for edge in edges:
                if edge["task"] == key:
                    result.append(edge)
                    pending.append(edge["blocker"])
    else:
        selected_id = snapshot_identity(package, snapshot)
        if command == "tasks" and snapshot != "primary":
            raise ValueError("tasks use an owner snapshot, not a source snapshot")
        dataset = {"tasks": "trackers/beads-export.jsonl", "symbols": "structure/symbols.jsonl",
                   "references": "reports/references.jsonl", "neighbors": "structure/dependency_edges.jsonl",
                   "candidate-evidence": "reports/candidate-evidence.jsonl",
                   "differences": "reports/snapshot-differences.jsonl"}[command]
        path = package / dataset
        if snapshot != "primary" and command in {"symbols", "neighbors", "references"}:
            path = package / "snapshots" / snapshot / dataset
            if not path.exists():
                raise ValueError(f"selected snapshot {command} are unavailable: {snapshot}")
        if command in {"references", "candidate-evidence"} and not path.exists():
            raise ValueError(f"{command} dataset is unavailable")
        result = []
        if path.exists():
            with path.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    if command in {"references", "candidate-evidence"} and selected_id is not None and row.get("snapshot_id") != selected_id:
                        continue
                    if command == "candidate-evidence" and not row.get("evidence_id"):
                        continue
                    if command == "differences" and snapshot != "primary" and row.get("snapshot") != snapshot:
                        continue
                    fields = {"tasks": [row.get("id")], "symbols": [row.get("name"), row.get("qualified_name")],
                              "references": [row.get("name")], "neighbors": [row.get("from"), row.get("to")],
                              "candidate-evidence": [row.get("evidence_id")],
                              "differences": [row.get("path"), row.get("snapshot")]}[command]
                    if value is None or value in fields:
                        result.append(row)
    response = {"rows": result[offset:offset + limit], "total": len(result),
                "next_offset": offset + limit if offset + limit < len(result) else None}
    if command == "candidate-evidence":
        coverage = package / "reports/coverage.json"
        declared = json.loads(coverage.read_text()).get("candidate_evidence") if coverage.is_file() else None
        response["evidence_coverage"] = declared or {
            "status": "legacy_report_binding_unavailable",
            "interpretation": "Rows without evidence IDs are omitted; older packages do not declare candidate binding coverage.",
        }
    return response


def text_query(package: Path, command: str, query: str, *, path: str | None,
               commit: str | None, limit: int, offset: int) -> dict:
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("limit must be 1..1000; offset must be nonnegative")
    def records():
        if command == "search":
            for source_path in sorted((package / "source").rglob("*")):
                if not source_path.is_file():
                    continue
                try:
                    content = source_path.read_text()
                except (UnicodeError, OSError):
                    continue
                at = content.casefold().find(query.casefold())
                if at >= 0:
                    yield {"kind": "source", "reference": str(source_path.relative_to(package)),
                           "line": content.count("\n", 0, at) + 1, "excerpt": content[max(0, at - 100):at + len(query) + 100]}
        for reference, line, text in evidence_rows(package):
            if command == "history" and not reference.startswith("history/"):
                continue
            if query.casefold() not in text.casefold() or path and path.casefold() not in text.casefold() or commit and commit.casefold() not in text.casefold():
                continue
            yield {"kind": "record", "reference": reference, "line": line,
                   "record": json.loads(text) if command == "history" else text[:2000]}
    selected = []
    count = 0
    for record in records():
        if offset <= count < offset + limit:
            selected.append(record)
        count += 1
        if count > offset + limit:
            break
    return {"rows": selected, "next_offset": offset + limit if count > offset + limit else None,
            "count_scope": "bounded scan; next_offset indicates additional matches"}


def reconstruct_snapshot(package: Path, snapshot: str, output: Path) -> None:
    if snapshot in {".", ".."} or "/" in snapshot or "\\" in snapshot:
        raise ValueError("invalid snapshot")
    if output.exists():
        raise ValueError("reconstruction output already exists")
    overlay = package / "snapshots" / snapshot
    manifest = json.loads((overlay / "manifest.json").read_text())
    output.mkdir(parents=True)
    for record in manifest["files"]:
        if not record["included"]:
            continue
        relative = record["path"]
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("unsafe snapshot path")
        source = (overlay / "files" if relative in manifest["changed"] else package / "source") / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("snapshot content mismatch")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if record.get("mode") is not None:
            target.chmod(record["mode"])


def history(package: Path, path: str | None, commit: str | None) -> int:
    if not (package / "index.sqlite3").is_file():
        found = 0
        for dataset, line_no, record in evidence_rows(package):
            if not dataset.startswith("history/"):
                continue
            if path and path.casefold() not in record.casefold():
                continue
            if commit and commit.casefold() not in record.casefold():
                continue
            print(f"{dataset}:{line_no} {record}")
            found += 1
        return 0 if found else 1
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
    for flag, kind, default in (("--limit", int, 100), ("--offset", int, 0)):
        p.add_argument(flag, type=kind, default=default)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("source")
    p.add_argument("path")
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int, default=80)
    p.add_argument("--snapshot", default="primary")
    p = sub.add_parser("history")
    p.add_argument("--path")
    p.add_argument("--commit")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("sql")
    p.add_argument("query")
    p = sub.add_parser("reconstruct-snapshot")
    p.add_argument("snapshot")
    p.add_argument("--output", type=Path, required=True)
    for command in ("snapshots", "tasks", "blockers", "symbols", "references", "neighbors", "candidate-evidence", "differences"):
        p = sub.add_parser(command)
        p.add_argument("value", nargs="?")
        p.add_argument("--limit", type=int, default=100)
        p.add_argument("--offset", type=int, default=0)
        p.add_argument("--json", action="store_true")
        p.add_argument("--snapshot", default="primary")
    args = parser.parse_args()
    try:
        if args.command in {"search", "history"}:
            result = text_query(args.package, args.command, getattr(args, "query", ""),
                                path=getattr(args, "path", None), commit=getattr(args, "commit", None),
                                limit=args.limit, offset=args.offset)
            print(json.dumps(result, indent=2) if args.json else "\n".join(json.dumps(row) for row in result["rows"]))
            return 0 if result["rows"] else 1
        if args.command == "source":
            return source(args.package, args.path, args.start, args.end, args.snapshot)
        if args.command == "sql":
            return sql(args.package, args.query)
        if args.command == "reconstruct-snapshot":
            reconstruct_snapshot(args.package, args.snapshot, args.output)
            return 0
        result = query_records(args.package, args.command, args.value, args.limit, args.offset, args.snapshot)
        print(json.dumps(result, indent=2) if args.json else "\n".join(json.dumps(row) for row in result["rows"]))
        return 0
    except (OSError, ValueError, sqlite3.Error if sqlite3 is not None else RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
