"""Build a portable SQLite mirror and usage guide for a captured Chisel package.

This module is part of the producer environment. The copied ``browse.py`` is
self-contained and uses only Python's standard library.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


def _jsonl_files(package: Path) -> list[Path]:
    """Return canonical evidence streams, excluding arbitrary source JSONL."""
    candidates = [package / "inventory.jsonl"]
    for area in ("structure", "history", "trackers", "verification", "metrics"):
        root = package / area
        if root.exists():
            candidates.extend(root.rglob("*.jsonl"))
            candidates.extend(root.rglob("*.ndjson"))
    return sorted(path for path in candidates if path.is_file())


def _safe_relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    base = root.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"path escapes package: {path}")
    return path.relative_to(root).as_posix()


def _record_path(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    for key in ("path", "source_path", "repo_path", "file"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    return None


def _build_db(
    package: Path, db_path: Path, availability: dict[str, Any]
) -> dict[str, Any]:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE package_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE datasets(dataset TEXT NOT NULL, line INTEGER NOT NULL, record TEXT NOT NULL,
                                  PRIMARY KEY(dataset, line));
            CREATE INDEX datasets_name_idx ON datasets(dataset);
            CREATE TABLE source_files(path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL,
                                      sha256 TEXT NOT NULL, content TEXT);
            CREATE INDEX source_files_hash_idx ON source_files(sha256);
        """)
        conn.executemany(
            "INSERT INTO package_meta VALUES (?, ?)",
            [(k, json.dumps(v, sort_keys=True)) for k, v in availability.items()],
        )
        counts: dict[str, int] = {}
        inventory: dict[str, dict[str, Any]] = {}
        for path in _jsonl_files(package):
            rel = _safe_relative(path, package)
            dataset = rel
            count = 0
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line_no, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    # Preserve malformed records for evidence instead of silently dropping them.
                    value = None
                    try:
                        value = json.loads(line)
                        record = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    except json.JSONDecodeError:
                        record = json.dumps(
                            {"_parse_error": True, "raw": line.rstrip("\n")},
                            ensure_ascii=False,
                        )
                    conn.execute(
                        "INSERT INTO datasets VALUES (?, ?, ?)",
                        (dataset, line_no, record),
                    )
                    if dataset == "inventory.jsonl" and isinstance(value, dict):
                        item_path = value.get("path")
                        if isinstance(item_path, str):
                            inventory[item_path] = value
                    count += 1
                    source_path = _record_path(value)
                    if source_path:
                        conn.execute(
                            "INSERT OR IGNORE INTO package_meta VALUES (?, ?)",
                            ("referenced_path:" + source_path, json.dumps(dataset)),
                        )
            counts[dataset] = count
        conn.execute(
            "CREATE VIEW history AS SELECT record, dataset, line FROM datasets WHERE dataset LIKE 'history/%'"
        )
        conn.execute(
            "CREATE VIEW symbols AS SELECT record, dataset, line FROM datasets WHERE dataset LIKE 'structure/%' AND (dataset LIKE '%symbol%')"
        )
        # Capture metadata binds the package to the repository revision and dirty state.
        capture_path = package / "capture.json"
        capture: dict[str, Any] = {}
        if capture_path.is_file():
            try:
                capture = json.loads(capture_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid capture.json: {exc}") from exc
            if not isinstance(capture, dict):
                raise ValueError("capture.json must contain a JSON object")
            if isinstance(capture, dict):
                conn.executemany(
                    "INSERT OR REPLACE INTO package_meta VALUES (?, ?)",
                    [
                        ("capture:" + str(k), json.dumps(v, sort_keys=True))
                        for k, v in capture.items()
                    ],
                )

        # Validate each included inventory row before indexing available source.
        source_root = package / "source"
        for relative, item in inventory.items():
            if not item.get("included"):
                continue
            rel_path = Path(relative)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ValueError(f"inventory source path escapes package: {relative}")
            captured = source_root / rel_path
            if not captured.is_file():
                raise ValueError(
                    f"inventory includes missing captured source: {relative}"
                )
            expected_hash = item.get("sha256")
            if (
                expected_hash
                and hashlib.sha256(captured.read_bytes()).hexdigest() != expected_hash
            ):
                raise ValueError(
                    f"captured source hash differs from inventory: source/{relative}"
                )

        try:
            conn.execute(
                "CREATE VIRTUAL TABLE package_fts USING fts5(kind UNINDEXED, reference UNINDEXED, content)"
            )
            has_fts = True
        except sqlite3.OperationalError:
            has_fts = False
        if source_root.exists():
            for path in sorted(p for p in source_root.rglob("*") if p.is_file()):
                rel = _safe_relative(path, package)
                raw = path.read_bytes()
                try:
                    content = raw.decode("utf-8") if b"\0" not in raw else None
                except UnicodeDecodeError:
                    content = None
                conn.execute(
                    "INSERT INTO source_files VALUES (?, ?, ?, ?)",
                    (rel, len(raw), hashlib.sha256(raw).hexdigest(), content),
                )
                if has_fts and content is not None:
                    conn.execute(
                        "INSERT INTO package_fts(kind, reference, content) VALUES (?, ?, ?)",
                        ("source", rel, content),
                    )
        if has_fts:
            for row in conn.execute("SELECT dataset, line, record FROM datasets"):
                conn.execute(
                    "INSERT INTO package_fts(kind, reference, content) VALUES (?, ?, ?)",
                    ("record", f"{row['dataset']}:{row['line']}", row["record"]),
                )
        conn.execute(
            "INSERT OR REPLACE INTO package_meta VALUES (?, ?)",
            ("dataset_counts", json.dumps(counts, sort_keys=True)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO package_meta VALUES (?, ?)",
            ("package_fts", json.dumps(has_fts)),
        )
        conn.commit()
        return {
            "datasets": counts,
            "source_files": conn.execute(
                "SELECT count(*) FROM source_files"
            ).fetchone()[0],
            "package_fts": has_fts,
            "capture": capture,
        }
    finally:
        conn.close()


def _guide(
    package: Path,
    *,
    project: str,
    snapshot_id: str,
    generated_at: str,
    availability: dict[str, Any],
    db_info: dict[str, Any],
) -> str:
    layout = [p.relative_to(package).as_posix() for p in sorted(package.iterdir())]
    dataset_rows = (
        "\n".join(
            f"- `{name}`: {count} records"
            for name, count in sorted(db_info["datasets"].items())
        )
        or "- No JSONL datasets were captured."
    )
    status = "\n".join(
        f"- `{key}`: {json.dumps(value, ensure_ascii=False)}"
        for key, value in sorted(availability.items())
    )
    capture = db_info.get("capture") or {}
    capture_status = (
        f"- Captured revision: `{capture.get('revision') or 'unavailable'}`\n"
        f"- Working tree dirty at capture: `{capture.get('dirty') if capture.get('dirty') is not None else 'unknown'}`\n"
        f"- Capture policy: `{capture.get('policy_version') or 'unavailable'}`"
    )
    return f"""# {project} Chisel package

This package contains captured project material and mechanically derived indexes. It does not contain an AI-written project assessment.

- Snapshot ID: `{snapshot_id}`
- Generated at: `{generated_at}`
- Project: `{project}`

{capture_status}

## Start offline

Run `python3 browse.py --package . --help`. The helper uses only Python's standard library. Common commands:

- `python3 browse.py --package . search 'pattern'` searches captured source text (FTS5 over source and evidence records when available, literal fallback otherwise).
- `python3 browse.py --package . source path/to/file.py --start 20 --end 45` prints a bounded source range.
- `python3 browse.py --package . history --path path/to/file.py` lists matching history records; add `--commit HASH` to select a commit.
- `python3 browse.py --package . sql 'SELECT dataset, count(*) FROM datasets GROUP BY dataset'` runs a read-only SELECT.

## Package layout

{chr(10).join(f'- `{name}/`' if (package/name).is_dir() else f'- `{name}`' for name in layout)}

`source/` is the directly browsable captured source tree when present. XML snapshots and compressed views are alternate representations; they are generated from selected memberships and can omit files outside those memberships. Use `inventory.jsonl` and `capture.json` for per-file role, inclusion/exclusion, digest, and capture-state evidence when supplied. The Git bundle, when present, retains repository-native reachable history. History JSONL is a searchable derivative, not a substitute for the bundle.

## Derived datasets

{dataset_rows}

`index.sqlite3` mirrors canonical JSONL/NDJSON evidence streams in `datasets(dataset, line, record)` and captured source metadata in `source_files`; it may include `package_fts` for source and evidence text search. It excludes arbitrary JSONL files under `source/`. JSONL remains the inspectable source representation. The `history` and `symbols` views are convenience views over records, not authoritative new claims.

## Coverage and method

{status}

The inventory and structure/history/trackers/verification datasets are only available when listed above. Missing datasets mean unavailable or not exported, never zero. A configured test command is not evidence of an executed test. Verification records should identify the tested revision and outcome where the owner provides them. Source statistics must use the explicit file role and captured membership; documentation, scratchpads, generated evidence, fixtures, and unclassified files must not be treated as maintained implementation. Parser-derived symbols and relations cover only the supported languages and extractors. No inferred call graph or quality score is supplied.

Files in context or scratch areas are preserved as project context with their paths; their presence alone does not establish that their contents are current or authoritative. Compare the stated revision and dirty state before relying on them.
"""


def build_offline_package(
    package_dir: Path, *, project: str, snapshot_id: str, generated_at: str
) -> dict[str, Any]:
    """Build an offline SQLite index, self-contained helper, and factual guide."""
    package = Path(package_dir)
    package.mkdir(parents=True, exist_ok=True)
    expected = (
        "inventory.jsonl",
        "capture.json",
        "structure",
        "history",
        "trackers",
        "verification",
        "metrics",
    )
    availability: dict[str, Any] = {}
    for item in expected:
        path = package / item
        availability[item] = {
            "available": path.exists(),
            "records": sum(
                1
                for p in path.rglob("*") if p.suffix in {".jsonl", ".ndjson"} and p.is_file()
                for _ in p.open(encoding="utf-8", errors="replace")
            )
            if path.is_dir()
            else None,
        }
    db_info = _build_db(package, package / "index.sqlite3", availability)
    helper_source = Path(__file__).with_name("chisel_browse.py")
    shutil.copyfile(helper_source, package / "browse.py")
    (package / "START_HERE.md").write_text(
        _guide(
            package,
            project=project,
            snapshot_id=snapshot_id,
            generated_at=generated_at,
            availability=availability,
            db_info=db_info,
        ),
        encoding="utf-8",
    )
    return {
        "database": "index.sqlite3",
        "helper": "browse.py",
        "guide": "START_HERE.md",
        "availability": availability,
        **db_info,
    }
