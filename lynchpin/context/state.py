from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.config import get_config
from ..periods import parse_period
from .bundles import build_period_evidence_bundle


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def cli() -> None:
    cfg = get_config()
    parser = argparse.ArgumentParser(description="Build a Lynchpin evidence bundle for a narrative period.")
    parser.add_argument("--scale", default="day", help="Narrative scale: day, week, month, quarter, half, year.")
    parser.add_argument("--key", help="Period key, e.g. 2026-03-16, 2026-W12, 2026-03, 2026-Q1, 2026-H1, 2026.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path.",
    )
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON packet to stdout.")
    args = parser.parse_args()

    scale = str(args.scale).strip().lower()
    key = args.key or _default_key(scale)
    period = parse_period(scale, key)
    if period is None:
        raise SystemExit(f"Unsupported period: scale={scale!r} key={key!r}")

    bundle = build_period_evidence_bundle(scale, key, write=True)
    payload = bundle.to_dict()
    output = Path(args.output) if args.output else cfg.repo_root / "artefacts/context/bundles" / f"{scale}-{key.replace('/', '_')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    if args.stdout:
        print(text)


def _default_key(scale: str) -> str:
    now = datetime.now()
    if scale == "day":
        return now.date().isoformat()
    if scale == "week":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if scale == "month":
        return now.strftime("%Y-%m")
    if scale == "quarter":
        return f"{now.year}-Q{((now.month - 1) // 3) + 1}"
    if scale in {"half", "half-year", "halfyear"}:
        return f"{now.year}-H{'1' if now.month <= 6 else '2'}"
    if scale == "year":
        return str(now.year)
    raise SystemExit(f"Unsupported scale {scale!r}")


if __name__ == "__main__":
    cli()
