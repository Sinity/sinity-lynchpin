"""Materialize ActivityWatch backup databases into the processed data lake."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import typer

from ..core.config import get_config

app = typer.Typer(add_completion=False)


@app.command()
def main(
    input_root: Path = typer.Option(Path("/realm/data/exports/activitywatch"), "--input"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    audit_only: bool = typer.Option(False, "--audit-only"),
) -> None:
    cfg = get_config()
    out = output_dir or cfg.activitywatch_archive_db_dir
    report = (
        audit_activitywatch_archive_dbs(out)
        if audit_only
        else process_activitywatch_archives(input_root=input_root, output_dir=out, dry_run=dry_run)
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")


def process_activitywatch_archives(*, input_root: Path, output_dir: Path, dry_run: bool = False) -> dict[str, object]:
    archives = _discover_archives(input_root)
    rows: list[dict[str, object]] = []
    for archive in archives:
        rows.extend(_extract_archive_dbs(archive, output_dir=output_dir, dry_run=dry_run))
    return {
        "input_root": str(input_root),
        "output_dir": str(output_dir),
        "archives": len(archives),
        "databases": len(rows),
        "details": rows,
        "dry_run": dry_run,
    }


def _discover_archives(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if _is_supported_archive(root) else []
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and _is_supported_archive(path))


def _extract_archive_dbs(path: Path, *, output_dir: Path, dry_run: bool) -> list[dict[str, object]]:
    if _is_zstd_tar(path):
        return _extract_zstd_tar_dbs(path, output_dir=output_dir, dry_run=dry_run)
    try:
        with tarfile.open(path, "r:*") as tf:
            rows = []
            for member in tf.getmembers():
                if not member.isfile() or not _looks_like_aw_sqlite(member.name):
                    continue
                target = output_dir / _db_name(path, member.name)
                row = {"archive": str(path), "member": member.name, "target": str(target), "written": not dry_run}
                if dry_run:
                    row["schema_status"] = "candidate"
                    rows.append(row)
                    continue
                output_dir.mkdir(parents=True, exist_ok=True)
                handle = tf.extractfile(member)
                if handle is None:
                    continue
                with target.open("wb") as out:
                    shutil.copyfileobj(handle, out)
                schema = _sqlite_schema_status(target)
                row.update(schema)
                if schema["schema_status"] != "activitywatch":
                    target.unlink(missing_ok=True)
                    row["written"] = False
                rows.append(row)
            return rows
    except (OSError, tarfile.TarError):
        return []


def _extract_zstd_tar_dbs(path: Path, *, output_dir: Path, dry_run: bool) -> list[dict[str, object]]:
    try:
        listing = subprocess.run(
            ["tar", "--zstd", "-tf", str(path)],
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, object]] = []
    for member in listing:
        if not _looks_like_aw_sqlite(member):
            continue
        target = output_dir / _db_name(path, member)
        row = {"archive": str(path), "member": member, "target": str(target), "written": not dry_run}
        if dry_run:
            row["schema_status"] = "candidate"
            rows.append(row)
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        extracted = subprocess.run(
            ["tar", "--zstd", "-xOf", str(path), member],
            check=True,
            capture_output=True,
            timeout=300,
        ).stdout
        target.write_bytes(extracted)
        schema = _sqlite_schema_status(target)
        row.update(schema)
        if schema["schema_status"] != "activitywatch":
            target.unlink(missing_ok=True)
            row["written"] = False
        rows.append(row)
    return rows


def _looks_like_aw_sqlite(name: str) -> bool:
    normalized = name.replace("\\", "/")
    lowered = normalized.lower()
    if "activitywatch" not in lowered and "aw-server" not in lowered:
        return False
    return lowered.endswith((".db", ".sqlite")) or "/sqlite.db" in lowered or "sqlite.db.backup" in lowered


def audit_activitywatch_archive_dbs(root: Path | None = None) -> dict[str, object]:
    cfg = get_config()
    base = root or cfg.activitywatch_archive_db_dir
    details = []
    paths = sorted((*base.glob("*.db"), *base.glob("*.sqlite"))) if base.exists() else ()
    for path in paths:
        details.append({"path": str(path), **_sqlite_schema_status(path)})
    return {
        "root": str(base),
        "databases": len(details),
        "accepted": sum(1 for row in details if row["schema_status"] == "activitywatch"),
        "rejected": sum(1 for row in details if row["schema_status"] != "activitywatch"),
        "details": details,
    }


def _sqlite_schema_status(path: Path) -> dict[str, object]:
    try:
        with sqlite3.connect(path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            if not {"events", "buckets"}.issubset(tables):
                return {"schema_status": "rejected", "reason": "missing events/buckets tables"}
            event_cols = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            bucket_cols = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(buckets)").fetchall()
            }
            required_events = {"bucketrow", "starttime", "endtime", "data"}
            if not required_events.issubset(event_cols) or "name" not in bucket_cols:
                return {"schema_status": "rejected", "reason": "missing ActivityWatch event columns"}
            count = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            return {"schema_status": "activitywatch", "event_count": count}
    except sqlite3.Error as exc:
        return {"schema_status": "rejected", "reason": str(exc)}


def _db_name(archive: Path, member: str) -> str:
    stem = archive.name
    for suffix in (".tar.zst", ".tzst", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    member_stem = Path(member).name.replace(".", "_")
    return f"{stem}_{member_stem}.db"


def _is_supported_archive(path: Path) -> bool:
    return _is_zstd_tar(path) or "".join(path.suffixes).lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2"))


def _is_zstd_tar(path: Path) -> bool:
    return "".join(path.suffixes).lower().endswith((".tar.zst", ".tzst"))


if __name__ == "__main__":
    app()
