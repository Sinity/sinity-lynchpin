"""Helpers for knowledgebase-hosted reference bundles.

The personal-productivity bundle corpus is mined as a reference library for
analysis design, prose synthesis, and enriched history context. This module
discovers zip bundles, normalizes duplicate names, exposes lightweight readers
for summary/report/table members, and can materialize a stable catalog artifact
for downstream dashboards.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ._utils.io import save_json

_DUPLICATE_RE = re.compile(r" \((\d+)\)$")


@dataclass(frozen=True)
class BundleRecord:
    canonical_name: str
    filename: str
    path: str
    duplicate_index: int
    size_bytes: int
    modified_at_utc: str
    member_count: int
    root_prefix: str | None
    summary_member: str | None
    report_member: str | None
    readme_member: str | None
    table_members: tuple[str, ...]
    chart_members: tuple[str, ...]


def default_bundle_root() -> Path:
    return get_config().knowledgebase_root / "personal-productivity-analyses"


def default_unpacked_root() -> Path:
    return get_config().analysis_output_dir / "reference_bundles"


def _canonicalize_bundle_name(stem: str) -> tuple[str, int]:
    match = _DUPLICATE_RE.search(stem)
    duplicate_index = int(match.group(1)) if match else 0
    canonical_name = _DUPLICATE_RE.sub("", stem)
    return canonical_name, duplicate_index


def _member_root_prefix(names: list[str]) -> str | None:
    for name in names:
        stripped = name.rstrip("/")
        if "/" not in stripped:
            continue
        first = stripped.split("/", 1)[0]
        if first:
            return first
    return None


def _find_member(names: list[str], suffix: str) -> str | None:
    suffix = suffix.lstrip("/")
    for name in names:
        if name.endswith(suffix):
            return name
    return None


def _bundle_record(path: Path) -> BundleRecord:
    with zipfile.ZipFile(path) as zf:
        names = sorted(zf.namelist())
    canonical_name, duplicate_index = _canonicalize_bundle_name(path.stem)
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return BundleRecord(
        canonical_name=canonical_name,
        filename=path.name,
        path=str(path),
        duplicate_index=duplicate_index,
        size_bytes=stat.st_size,
        modified_at_utc=modified,
        member_count=len(names),
        root_prefix=_member_root_prefix(names),
        summary_member=_find_member(names, "summary.json"),
        report_member=_find_member(names, "report.md"),
        readme_member=_find_member(names, "README.md"),
        table_members=tuple(name for name in names if "/tables/" in name and not name.endswith("/")),
        chart_members=tuple(name for name in names if "/charts/" in name and not name.endswith("/")),
    )


def discover_bundle_records(root: str | Path | None = None) -> list[BundleRecord]:
    bundle_root = Path(root) if root is not None else default_bundle_root()
    if not bundle_root.exists():
        return []
    return sorted(
        (_bundle_record(path) for path in bundle_root.glob("*.zip")),
        key=lambda record: (record.canonical_name, record.filename),
    )


def grouped_bundle_records(records: list[BundleRecord]) -> dict[str, list[BundleRecord]]:
    grouped: dict[str, list[BundleRecord]] = {}
    for record in records:
        grouped.setdefault(record.canonical_name, []).append(record)
    for name in grouped:
        grouped[name] = sorted(
            grouped[name],
            key=lambda item: (
                item.modified_at_utc,
                -item.size_bytes,
                -item.member_count,
                -item.duplicate_index,
            ),
            reverse=True,
        )
    return grouped


def preferred_bundle(records: list[BundleRecord], canonical_name: str) -> BundleRecord | None:
    grouped = grouped_bundle_records(records)
    choices = grouped.get(canonical_name, [])
    return choices[0] if choices else None


def _open_zip(record: BundleRecord) -> zipfile.ZipFile:
    return zipfile.ZipFile(record.path)


def read_bundle_json(record: BundleRecord, member: str | None = None, *, suffix: str | None = None) -> Any:
    target = member or suffix
    if target is None:
        raise ValueError("member or suffix is required")
    with _open_zip(record) as zf:
        actual = target if member else _find_member(zf.namelist(), suffix or "")
        if actual is None:
            raise FileNotFoundError(f"{record.filename}: missing member ending with {suffix!r}")
        return json.loads(zf.read(actual))


def read_bundle_text(record: BundleRecord, member: str | None = None, *, suffix: str | None = None) -> str:
    target = member or suffix
    if target is None:
        raise ValueError("member or suffix is required")
    with _open_zip(record) as zf:
        actual = target if member else _find_member(zf.namelist(), suffix or "")
        if actual is None:
            raise FileNotFoundError(f"{record.filename}: missing member ending with {suffix!r}")
        return zf.read(actual).decode("utf-8", errors="replace")


def read_bundle_csv_rows(record: BundleRecord, *, suffix: str) -> list[dict[str, str]]:
    with _open_zip(record) as zf:
        actual = _find_member(zf.namelist(), suffix)
        if actual is None:
            raise FileNotFoundError(f"{record.filename}: missing CSV member ending with {suffix!r}")
        text = zf.read(actual).decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def ensure_unpacked_bundle(record: BundleRecord, *, root: str | Path | None = None) -> Path:
    unpack_root = Path(root) if root is not None else default_unpacked_root()
    destination = unpack_root / Path(record.filename).stem
    marker_path = destination / ".bundle-source.json"
    expected = {
        "filename": record.filename,
        "path": record.path,
        "size_bytes": record.size_bytes,
        "modified_at_utc": record.modified_at_utc,
    }
    if marker_path.exists():
        try:
            current = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            current = None
        if current == expected:
            return destination

    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(record.path) as zf:
        zf.extractall(destination)
    marker_path.write_text(json.dumps(expected, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def unpack_preferred_bundle(
    canonical_name: str,
    *,
    bundle_root: str | Path | None = None,
    root: str | Path | None = None,
) -> Path:
    records = discover_bundle_records(bundle_root)
    record = preferred_bundle(records, canonical_name)
    if record is None:
        raise FileNotFoundError(f"Missing reference bundle: {canonical_name}")
    return ensure_unpacked_bundle(record, root=root)


def build_bundle_catalog(out_file: str | Path, *, root: str | Path | None = None) -> dict[str, Any]:
    records = discover_bundle_records(root)
    grouped = grouped_bundle_records(records)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bundle_root": str(Path(root) if root is not None else default_bundle_root()),
        "bundle_count": len(records),
        "canonical_bundle_count": len(grouped),
        "bundles": [asdict(record) for record in records],
        "canonical_groups": {
            name: {
                "preferred": asdict(items[0]),
                "duplicates": [asdict(item) for item in items[1:]],
            }
            for name, items in grouped.items()
        },
    }
    save_json(out_file, payload, sort_keys=True)
    return payload
