"""Materialize canonical title/window classification metadata."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import SchemaVersionError, SourceUnavailableError
from ..core.io import latest_mtime_iso
from ..sources.title_metadata import title_metadata_path
from ._manifest import write_manifest


TITLE_METADATA_SCHEMA_VERSION = 1


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

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on devshell packaging
        raise SourceUnavailableError("duckdb", reason="duckdb is required to materialize title metadata") from exc

    row_count = 0
    source_counts: Counter[str] = Counter()
    model_versions: Counter[str] = Counter()
    with duckdb.connect(str(db), read_only=True) as conn:
        table = _select_source_table(conn)
        result = conn.execute(f"SELECT * FROM {table} ORDER BY title_hash")
        columns = [str(desc[0]) for desc in result.description]
        with output.open("w", encoding="utf-8") as handle:
            while True:
                rows = result.fetchmany(10_000)
                if not rows:
                    break
                for raw_row in rows:
                    payload = _canonical_payload(dict(zip(columns, raw_row)))
                    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                    row_count += 1
                    source = payload.get("classification_source")
                    version = payload.get("model_version")
                    if source:
                        source_counts[str(source)] += 1
                    if version:
                        model_versions[str(version)] += 1

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
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


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
