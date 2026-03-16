from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.config import get_config
from .packets import build_recent_state


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def cli() -> None:
    cfg = get_config()
    parser = argparse.ArgumentParser(description="Build compact Lynchpin context state from recent trajectory artifacts.")
    parser.add_argument("--days", type=int, default=14, help="Lookback window in days.")
    parser.add_argument("--end", type=_parse_datetime, help="Optional ISO timestamp for the window end.")
    parser.add_argument(
        "--output",
        type=Path,
        default=cfg.repo_root / "artefacts/trajectory/context/recent-14d.json",
        help="Output JSON path.",
    )
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON packet to stdout.")
    args = parser.parse_args()

    packet = build_recent_state(days=args.days, end=args.end)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(packet, indent=2, sort_keys=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    if args.stdout:
        print(text)


if __name__ == "__main__":
    cli()
