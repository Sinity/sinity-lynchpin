"""Materialize canonical Lynchpin products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..materialization import (
    audit_materialization,
    plan_materializations,
    run_materialization_plan,
)

_PROGRESS_FORMAT = "plain"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical Lynchpin products")
    parser.add_argument("--all", action="store_true", help="materialize every locally rebuildable product")
    parser.add_argument("--promote", action="store_true", help="also build/promote a coherent substrate snapshot")
    parser.add_argument("--start", help="snapshot start date when --promote is used")
    parser.add_argument("--end", help="snapshot end date when --promote is used")
    parser.add_argument("--history", choices=("window", "all"), default="window", help="promote an explicit window or the full ready contract range")
    parser.add_argument("--weak-tags", action="store_true", help="include weak keyword/proximity evidence tags in promoted snapshot")
    parser.add_argument("--snapshot-output", type=Path, default=None, help="write promoted snapshot Markdown to this path")
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any known product is not ready")
    parser.add_argument("--json", action="store_true", help="write JSON audit rows after materialization")
    parser.add_argument("--plan-json", action="store_true", help="write materialization plan rows before execution")
    parser.add_argument("--force", action="store_true", help="rebuild all locally materializable products")
    parser.add_argument("--progress", choices=("plain", "json", "quiet"), default="plain")
    args = parser.parse_args(argv)
    global _PROGRESS_FORMAT
    _PROGRESS_FORMAT = args.progress

    if not args.all:
        parser.error("only --all is supported; canonical products are materialized as a coherent set")

    _progress("planning canonical materialization")
    plan = plan_materializations(force=args.force)
    _progress(f"plan ready: {len(plan)} step(s)")
    if args.plan_json:
        sys.stdout.write(json.dumps([step.to_json() for step in plan], indent=2, sort_keys=True) + "\n")
    for step in plan:
        _progress(f"{step.action}: {step.name} ({step.reason})")
    run_materialization_plan(plan)
    _progress("canonical materialization complete")
    if args.promote:
        if args.history == "all":
            start_d, end_d = _all_history_window()
            args.start = start_d.isoformat()
            args.end = end_d.isoformat()
            _progress(f"derived all-history promotion window: {args.start}..{args.end}")
        elif not args.start or not args.end:
            parser.error("--promote requires --start and --end unless --history all is used")
        date.fromisoformat(args.start)
        date.fromisoformat(args.end)
        from .substrate_snapshot import main as snapshot_main

        snapshot_output = args.snapshot_output or (
            get_config().local_root / "generated" / "substrate_snapshot.md"
        )
        forwarded = [
            "--start",
            args.start,
            "--end",
            args.end,
            "--output",
            str(snapshot_output),
            "--progress",
            args.progress,
        ]
        if args.weak_tags:
            forwarded.append("--weak-tags")
        _progress(f"promoting substrate snapshot: {args.start}..{args.end}")
        code = snapshot_main(forwarded)
        if code:
            _progress(f"substrate snapshot failed with exit code {code}")
            return code
        _progress("substrate snapshot promotion complete")
    _progress("auditing materialization readiness")
    rows = audit_materialization()
    if args.json:
        sys.stdout.write(json.dumps([row.to_json() for row in rows], indent=2, sort_keys=True) + "\n")
    if args.strict and any(row.status != "ready" for row in rows):
        return 1
    return 0


def _progress(message: str) -> None:
    if _PROGRESS_FORMAT == "quiet":
        return
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    if _PROGRESS_FORMAT == "json":
        sys.stderr.write(json.dumps({"ts": stamp, "component": "materialize", "message": message}, sort_keys=True) + "\n")
    else:
        sys.stderr.write(f"[{stamp}] materialize: {message}\n")
    sys.stderr.flush()


def _all_history_window() -> tuple[date, date]:
    from datetime import timedelta

    rows = [
        row
        for row in audit_materialization()
        if row.status == "ready" and row.first_date is not None and row.last_date is not None
    ]
    if not rows:
        raise MaterializationError(
            "materialized-datasets",
            reason="no ready materialized dataset has date bounds",
        )
    start = min(row.first_date for row in rows if row.first_date is not None)
    last = max(row.last_date for row in rows if row.last_date is not None)
    return start, last + timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
