"""Inventory raw Google Takeout archives."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..sources.google_takeout import archive_inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = archive_inventory(args.root)
    if args.json:
        payload = [
            {
                **asdict(row),
                "path": str(row.path),
                "product_counts": dict(row.product_counts),
            }
            for row in rows
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"archives\t{len(rows)}")
    for row in rows:
        products = ",".join(f"{name}:{count}" for name, count in row.product_counts)
        print(
            f"{row.path.name}\tmembers={row.member_count}\t"
            f"bytes={row.size_bytes}\tchrome_history={row.chrome_history_members}\t"
            f"products={products}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
