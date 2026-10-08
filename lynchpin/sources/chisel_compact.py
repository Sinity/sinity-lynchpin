"""Compact large Chisel evidence streams without changing their records."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TextIO


def open_text(path: Path) -> TextIO:
    """Open a canonical text stream in either supported package representation."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def resolved_stream(path: Path) -> Path:
    """Find a stream in a current or older package without hiding absence."""
    return path if path.is_file() else path.with_name(path.name + ".gz")


def compact_jsonl(package: Path) -> dict[str, object]:
    """Replace derived JSONL with deterministic gzip streams before manifesting."""
    package = Path(package)
    roots = [package / name for name in ("structure", "reports", "history")]
    roots.extend(sorted((package / "snapshots").glob("*/structure")))
    records: list[dict[str, object]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for source in sorted((*root.rglob("*.jsonl"), *root.rglob("*.ndjson"))):
            target = source.with_name(source.name + ".gz")
            if target.exists():
                raise ValueError(f"compacted dataset already exists: {target}")
            raw_hash = hashlib.sha256()
            raw_bytes = 0
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=root)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as original:
                    with gzip.GzipFile(filename="", fileobj=output, mode="wb", compresslevel=3, mtime=0) as packed:
                        while chunk := original.read(1024 * 1024):
                            raw_hash.update(chunk)
                            raw_bytes += len(chunk)
                            packed.write(chunk)
                os.replace(temporary, target)
                source.unlink()
            finally:
                temporary.unlink(missing_ok=True)
            with target.open("rb") as packed_file:
                packed_hash = hashlib.file_digest(packed_file, "sha256").hexdigest()
            records.append({
                "path": target.relative_to(package).as_posix(),
                "encoding": "gzip-jsonl",
                "source_bytes": raw_bytes,
                "source_sha256": raw_hash.hexdigest(),
                "bytes": target.stat().st_size,
                "sha256": packed_hash,
            })
    for root in roots:
        path = root / "graph_projections.json"
        if not path.is_file():
            continue
        projection = json.loads(path.read_text(encoding="utf-8"))
        if projection.get("edge_dataset") == "dependency_edges.jsonl" and (path.parent / "dependency_edges.jsonl.gz").is_file():
            projection["edge_dataset"] = "dependency_edges.jsonl.gz"
            path.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_coverage = package / "reports/coverage.json"
    if report_coverage.is_file():
        coverage = json.loads(report_coverage.read_text(encoding="utf-8"))
        if isinstance(coverage.get("dataset_coverage"), dict):
            physical = {str(row["path"]).removesuffix(".gz"): row["path"] for row in records}
            coverage["dataset_coverage"] = {
                physical.get(name, name): value
                for name, value in coverage["dataset_coverage"].items()
            }
            coverage["schema_version"] = 4
            coverage["dataset_representation"] = "paths name the stored package artifacts"
            report_coverage.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": 1, "method": "gzip level 3, deterministic header", "streams": records}
    (package / "dataset-compression.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"streams": len(records), "source_bytes": sum(int(r["source_bytes"]) for r in records),
            "stored_bytes": sum(int(r["bytes"]) for r in records)}
