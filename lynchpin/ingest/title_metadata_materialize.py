"""Materialize canonical title/window classification metadata."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.errors import SchemaVersionError, SourceUnavailableError
from ..core.io import latest_mtime_iso
from ..sources.title_metadata import title_metadata_path
from ._manifest import atomic_write_ndjson, write_manifest
from ..materializers.partition_store import ArtifactStore, ProductPartitionKey, deterministic_input_digest


TITLE_METADATA_SCHEMA_VERSION = 2


def materialize_title_metadata(
    *,
    source_db: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    db = source_db or _default_source_db()
    if db is None:
        raise FileNotFoundError("no historical title classification DuckDB found")
    output = output or title_metadata_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output.with_name(f".{output.stem}.partitions"))
    input_signature = _input_signature(db)
    if store.selection_is_readable() and store.metadata.get("input_signature") == input_signature and output.exists():
        return _read_manifest(output.with_suffix(".manifest.json"))
    _migrate_title_store(store, output, db)

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on devshell packaging
        raise SourceUnavailableError("duckdb", reason="duckdb is required to materialize title metadata") from exc

    row_count = 0
    rows_by_month: dict[str, list[dict[str, Any]]] = {}
    source_counts: Counter[str] = Counter()
    model_versions: Counter[str] = Counter()
    with duckdb.connect(str(db), read_only=True) as conn:
        table = _select_source_table(conn)
        result = conn.execute(f"SELECT * FROM {table} ORDER BY title_hash")
        description = result.description
        if description is None:
            raise RuntimeError(f"source table {table} has no result description")
        columns = [str(desc[0]) for desc in description]

        def metadata_rows() -> Iterator[dict[str, Any]]:
            nonlocal row_count
            while True:
                rows = result.fetchmany(10_000)
                if not rows:
                    break
                for raw_row in rows:
                    payload = _canonical_payload(dict(zip(columns, raw_row)))
                    row_count += 1
                    source = payload.get("classification_source")
                    version = payload.get("model_version")
                    if source:
                        source_counts[str(source)] += 1
                    if version:
                        model_versions[str(version)] += 1
                    rows_by_month.setdefault(_partition_month(db, payload), []).append(payload)
                    yield payload

        atomic_write_ndjson(output, metadata_rows())

    selected: dict[ProductPartitionKey, Any] = {}
    for month, rows in rows_by_month.items():
        key = ProductPartitionKey.month("title_metadata.classifications", month)
        selected[key] = store.put(
            key, _encode_rows(rows), format="ndjson", input_digest=input_signature,
            row_count=len(rows), publish=False,
        )
    store.publish(selected, metadata={"dataset": "lynchpin.title_metadata", "input_signature": input_signature})

    manifest = {
        "dataset": "lynchpin.title_metadata",
        "schema_version": TITLE_METADATA_SCHEMA_VERSION,
        "materialized_path": str(output),
        "source_db": str(db),
        "source_db_size_bytes": db.stat().st_size,
        "source_db_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
        "input_files": [str(db)],
        "input_file_count": 1,
        "input_latest_mtime": latest_mtime_iso((db,)),
        "source_table": table,
        "row_count": row_count,
        "source_counts": dict(sorted(source_counts.items())),
        "model_versions": dict(sorted(model_versions.items())),
        "partition_store": str(store.root),
        "partition_scheme": "month",
        "product_paths": {
            key.value: str(store.root / ref.path)
            for key, ref in sorted(selected.items(), key=lambda item: item[0].value)
        },
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def _partition_month(db: Path, payload: dict[str, Any]) -> str:
    for key in ("updated_at", "created_at", "classified_at", "timestamp"):
        value = payload.get(key)
        if value:
            text = str(value)
            if len(text) >= 7 and text[4] == "-":
                return text[:7]
    return datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).strftime("%Y-%m")


def _migrate_title_store(store: ArtifactStore, output: Path, db: Path) -> None:
    """Seed partitions from the old carrier before replacing any carrier bytes."""
    if store.manifest_path.exists() or not output.exists():
        return
    legacy_manifest = _read_manifest(output.with_suffix(".manifest.json"))
    legacy_rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    expected = legacy_manifest.get("row_count")
    if isinstance(expected, int) and expected != len(legacy_rows):
        raise RuntimeError("title metadata migration row-count validation failed")
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in legacy_rows:
        if isinstance(row, dict):
            by_month.setdefault(_partition_month(db, row), []).append(row)
    selected: dict[ProductPartitionKey, Any] = {}
    for month, rows in by_month.items():
        key = ProductPartitionKey.month("title_metadata.classifications", month)
        selected[key] = store.put(key, _encode_rows(rows), format="ndjson", row_count=len(rows), publish=False)
    if selected:
        store.publish(selected, metadata={"migration": "legacy-monolith", "validated": True})


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _input_signature(path: Path) -> str:
    stat = path.stat()
    return deterministic_input_digest([(str(path), stat.st_size, stat.st_mtime_ns)])


def _encode_rows(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()


def _default_source_db() -> Path | None:
    cfg = get_config()
    candidates = (
        cfg.local_root / "enrich/semantic_classifications.duckdb",
        cfg.local_root / "enrichment/semantic_classifications.duckdb",
        cfg.repo_root / ".lynchpin/enrich/semantic_classifications.duckdb",
        cfg.repo_root / ".lynchpin/enrichment/semantic_classifications.duckdb",
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def _select_source_table(conn: Any) -> str:
    rows = conn.execute("SHOW TABLES").fetchall()
    names = {str(row[0]) for row in rows}
    if "semantic_classifications_unified" in names:
        return "semantic_classifications_unified"
    if "gpt_classifications" in names:
        return "gpt_classifications"
    if "semantic_classifications" in names:
        return "semantic_classifications"
    raise SchemaVersionError(
        found=sorted(names),
        expected="semantic_classifications_unified | gpt_classifications | semantic_classifications",
        source="classification DuckDB",
    )


def _canonical_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {str(key): _json_value(value) for key, value in row.items()}
    confidence = payload.get("confidence")
    if isinstance(confidence, str):
        mapped = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(confidence.lower())
        if mapped is not None:
            payload["confidence"] = mapped
    return {key: value for key, value in payload.items() if value is not None}


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical title metadata")
    parser.add_argument("--source-db", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_title_metadata(source_db=args.source_db, output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
