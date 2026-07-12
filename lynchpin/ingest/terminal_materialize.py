"""Materialize canonical terminal history products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import date_to_dt_range, logical_date
from ..sources.terminal import canonical_atuin_history_path, commands_from_atuin_db
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import write_manifest


ATUIN_HISTORY_SCHEMA_VERSION = 1


CommandRow = dict[str, Any]


def materialize_atuin_history(
    *,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    cfg = get_config()
    output = output or canonical_atuin_history_path()
    if (start is None) != (end is None):
        raise MaterializationError("terminal_materialize", reason="Atuin history materialization requires both start and end")
    if start is not None and end is not None and end <= start:
        raise MaterializationError("terminal_materialize", reason="Atuin history materialization end must be after start")
    input_files = atuin_input_files(cfg)
    output.parent.mkdir(parents=True, exist_ok=True)

    query_window = _query_window(start, end)
    query_kwargs = {"start": query_window[0], "end": query_window[1]} if query_window is not None else {}

    window_rows: list[CommandRow] = []
    for command in commands_from_atuin_db(cfg.atuin_db, **query_kwargs):
        command_logical_date = logical_date(command.timestamp)
        if start is not None and end is not None and not (start <= command_logical_date < end):
            continue
        window_rows.append(_command_row(command))

    if start is not None and end is not None:
        rows = _merge_existing_rows(output=output, start=start, end=end, window_rows=window_rows)
        covered_dates = _merge_covered_dates(manifest=output.with_suffix(".manifest.json"), start=start, end=end)
    else:
        rows = window_rows
        covered_dates = tuple(sorted({_row_logical_date(row) for row in rows}))

    rows.sort(key=lambda row: str(row["timestamp"]))
    _write_ndjson(output, rows)

    timestamps = [_row_timestamp(row) for row in rows]

    manifest = {
        "dataset": "atuin.history",
        "schema_version": ATUIN_HISTORY_SCHEMA_VERSION,
        "materialized_path": str(output),
        "row_count": len(rows),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": min(timestamps).date().isoformat() if timestamps else None,
        "last_timestamp_date": max(timestamps).date().isoformat() if timestamps else None,
        "date_boundary": "logical_06:00_local",
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive" if start is not None and end is not None else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    manifest_path = output.with_suffix(".manifest.json")
    write_manifest(manifest_path, manifest)
    return manifest


def _command_row(command: Any) -> CommandRow:
    return {
        "timestamp": command.timestamp.isoformat(),
        "duration_ns": command.duration_ns,
        "exit_code": command.exit_code,
        "cwd": command.cwd,
        "command": command.command,
    }


def _merge_existing_rows(
    *,
    output: Path,
    start: date,
    end: date,
    window_rows: list[CommandRow],
) -> list[CommandRow]:
    outside_window = [
        row for row in _read_existing_rows(output)
        if not (start <= _row_logical_date(row) < end)
    ]
    return [*outside_window, *window_rows]


def _read_existing_rows(path: Path) -> list[CommandRow]:
    if not path.exists():
        return []
    rows: list[CommandRow] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get("timestamp"):
                rows.append(payload)
    return rows


def _write_ndjson(path: Path, rows: list[CommandRow]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _row_timestamp(row: CommandRow) -> datetime:
    return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))


def _row_logical_date(row: CommandRow) -> date:
    return logical_date(_row_timestamp(row))


def _merge_covered_dates(*, manifest: Path, start: date, end: date) -> tuple[date, ...]:
    return merge_manifest_covered_dates(manifest=manifest, start=start, end=end)


def _query_window(start: date | None, end: date | None) -> tuple[datetime, datetime] | None:
    if start is None or end is None:
        return None
    return date_to_dt_range(start, end - timedelta(days=1))


def atuin_input_files(cfg: Any) -> tuple[Path, ...]:
    db = Path(cfg.atuin_db)
    return (db,) if db.exists() else ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical terminal datasets")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    report = materialize_atuin_history(output=args.output, start=args.start, end=args.end)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
