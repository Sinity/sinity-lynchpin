"""Materialize ARBTT capture logs into canonical focus events."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.classify import resolve_project
from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.arbtt import ArbttFocusEvent, arbtt_events_path, arbtt_manifest_path

_HEADER_RE = re.compile(r"^(?P<stamp>\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d)")
_WINDOW_RE = re.compile(r"^\s*\((?P<active>\*| )\)\s+(?P<program>.*?):\s*(?P<title>.*)$")
ARBTT_EVENTS_SCHEMA_VERSION = 1


def materialize_arbtt_events(*, root: Path | None = None, output: Path | None = None) -> dict[str, Any]:
    root = root or get_config().arbtt_root
    output = output or arbtt_events_path(root)
    rows = list(_dedupe(_iter_events(root)))
    rows.sort(key=lambda row: (row.timestamp, row.event_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = asdict(row)
            payload["timestamp"] = row.timestamp.isoformat()
            payload["tags"] = list(row.tags)
            payload["caveats"] = list(row.caveats)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    input_files = _capture_logs(root)
    manifest = {
        "dataset": "focus.arbtt.events",
        "schema_version": ARBTT_EVENTS_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": len(rows),
        "first_date": rows[0].timestamp.date().isoformat() if rows else None,
        "last_date": rows[-1].timestamp.date().isoformat() if rows else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "arbtt_dump_available": shutil.which("arbtt-dump") is not None,
    }
    arbtt_manifest_path(root).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _capture_logs(root: Path) -> list[Path]:
    return sorted(root.rglob("capture.log")) if root.exists() else []


def _iter_events(root: Path) -> Iterator[ArbttFocusEvent]:
    for path in _capture_logs(root):
        yield from _dump_capture(path)


def _dump_capture(path: Path) -> Iterator[ArbttFocusEvent]:
    binary = shutil.which("arbtt-dump")
    if not binary:
        return
    proc = subprocess.run([binary, "--logfile", str(path)], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return
    current_stamp: datetime | None = None
    for line in proc.stdout.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            try:
                current_stamp = datetime.strptime(header.group("stamp"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                current_stamp = None
            continue
        if current_stamp is None:
            continue
        event = _parse_window_line(line, path, current_stamp)
        if event is not None:
            yield event


def _parse_window_line(line: str, path: Path, timestamp: datetime) -> ArbttFocusEvent | None:
    match = _WINDOW_RE.match(line)
    if not match:
        return None
    if match.group("active") != "*":
        return None
    duration = 60.0
    program = match.group("program").strip()
    title = match.group("title").strip()
    category = ""
    tags = tuple(re.findall(r"\$?([A-Za-z][A-Za-z0-9_-]+)", line.split("$", 1)[1] if "$" in line else ""))
    project = resolve_project(program, title)
    digest = hashlib.sha1(f"{timestamp.isoformat()}\0{program}\0{title}\0{path}".encode("utf-8", errors="replace")).hexdigest()
    return ArbttFocusEvent(
        event_id=digest,
        timestamp=timestamp,
        duration_s=duration,
        program=program,
        title=title,
        category=category,
        tags=tags,
        project=project,
        source_path=str(path),
        caveats=(),
    )


def _dedupe(rows: Iterator[ArbttFocusEvent]) -> Iterator[ArbttFocusEvent]:
    seen: set[str] = set()
    for row in rows:
        if row.event_id in seen:
            continue
        seen.add(row.event_id)
        yield row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ARBTT focus events")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    sys.stdout.write(json.dumps(materialize_arbtt_events(root=args.root, output=args.output), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
