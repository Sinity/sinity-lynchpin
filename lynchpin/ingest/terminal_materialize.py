"""Materialize canonical terminal history products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..sources.terminal import canonical_atuin_history_path, commands_from_atuin_db


def materialize_atuin_history(*, output: Path | None = None) -> dict[str, Any]:
    cfg = get_config()
    output = output or canonical_atuin_history_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    first = None
    last = None
    with output.open("w", encoding="utf-8") as handle:
        for command in commands_from_atuin_db(cfg.atuin_db):
            row = {
                "timestamp": command.timestamp.isoformat(),
                "duration_ns": command.duration_ns,
                "exit_code": command.exit_code,
                "cwd": command.cwd,
                "command": command.command,
            }
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
            if first is None or command.timestamp < first:
                first = command.timestamp
            if last is None or command.timestamp > last:
                last = command.timestamp

    manifest = {
        "dataset": "atuin.history",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": count,
        "first_date": first.date().isoformat() if first else None,
        "last_date": last.date().isoformat() if last else None,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical terminal datasets")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_atuin_history(output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
