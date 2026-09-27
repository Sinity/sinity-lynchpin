"""Manifest-selected canonical machine telemetry files."""

from __future__ import annotations

import json
from heapq import merge
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any


def machine_manifest_path(table_path: Path) -> Path:
    return table_path.with_name("manifest.json")


def load_machine_manifest(table_path: Path) -> dict[str, Any]:
    path = machine_manifest_path(table_path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def machine_table_files(table_path: Path, manifest: dict[str, Any]) -> tuple[Path, ...]:
    """Resolve one table's selected files, preserving v1 monolith packages."""
    if manifest.get("schema_version") != 2:
        return (table_path,)
    tables = manifest.get("tables")
    table = tables.get(table_path.stem) if isinstance(tables, dict) else None
    if not isinstance(table, dict):
        return ()
    files: list[Path] = []
    base = table.get("base_path")
    if isinstance(base, str):
        files.append(_package_path(table_path.parent, base))
    partitions = table.get("partitions")
    if isinstance(partitions, dict):
        for day in sorted(partitions):
            part = partitions[day]
            if isinstance(part, dict) and isinstance(part.get("path"), str):
                files.append(_package_path(table_path.parent, part["path"]))
    return tuple(files)


def machine_table_available(table_path: Path, manifest: dict[str, Any] | None = None) -> bool:
    selected = manifest if manifest is not None else load_machine_manifest(table_path)
    if selected.get("schema_version") == 2:
        tables = selected.get("tables")
        table = tables.get(table_path.stem) if isinstance(tables, dict) else None
        if not isinstance(table, dict) or not isinstance(table.get("partitions"), dict):
            return False
    files = machine_table_files(table_path, selected)
    return all(path.is_file() for path in files)


def iter_machine_table_rows(
    table_path: Path, *, start: date | None, end: date | None
) -> Iterator[dict[str, Any]]:
    manifest = load_machine_manifest(table_path)
    if manifest.get("schema_version") != 2:
        yield from _rows(table_path, start=start, end=end)
        return
    tables = manifest.get("tables")
    table = tables.get(table_path.stem) if isinstance(tables, dict) else None
    if not isinstance(table, dict) or not isinstance(table.get("partitions"), dict):
        raise FileNotFoundError(f"machine table missing from manifest: {table_path.stem}")
    partitions = table.get("partitions")
    parts = partitions if isinstance(partitions, dict) else {}
    def base_rows() -> Iterator[dict[str, Any]]:
        base = table.get("base_path")
        if isinstance(base, str):
            for row in _rows(_package_path(table_path.parent, base), start=start, end=end):
                if str(row["observed_at"])[:10] not in parts:
                    yield row

    def partition_rows() -> Iterator[dict[str, Any]]:
        for day in sorted(parts):
            if (start is not None and day < start.isoformat()) or (
                end is not None and day > end.isoformat()
            ):
                continue
            part = parts[day]
            if isinstance(part, dict) and isinstance(part.get("path"), str):
                yield from _rows(_package_path(table_path.parent, part["path"]), start=start, end=end)

    yield from merge(base_rows(), partition_rows(), key=lambda row: str(row["observed_at"]))


def _package_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"invalid machine package path: {relative}")
    return root / path


def _rows(path: Path, *, start: date | None, end: date | None) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            day = str(row.get("observed_at", ""))[:10]
            if not day or (start is not None and day < start.isoformat()):
                continue
            if end is not None and day > end.isoformat():
                continue
            yield row
