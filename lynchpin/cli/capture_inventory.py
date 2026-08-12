"""Itemize capture roots that have no dedicated source module.

Run with ``python -m lynchpin.cli.capture_inventory`` for a plain-text
summary, or ``--json`` for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..sources.capture_inventory import capture_inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    items = capture_inventory(args.captures_root)
    if args.json:
        payload = [
            {
                **asdict(item),
                "path": str(item.path),
                "earliest": item.earliest.isoformat() if item.earliest else None,
                "latest": item.latest.isoformat() if item.latest else None,
            }
            for item in items
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for item in items:
        status = "missing" if not item.exists else ("empty" if item.file_count == 0 else "populated")
        span = f"{item.earliest.date()}..{item.latest.date()}" if item.earliest and item.latest else "-"
        print(
            f"{item.id:16s} {status:10s} kind={item.kind:16s} "
            f"files={item.file_count:<7d} bytes={item.total_bytes:<14,d} span={span}"
        )
        print(f"{'':16s} {item.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
