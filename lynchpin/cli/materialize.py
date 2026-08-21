"""Materialize canonical Lynchpin products."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
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


class _CandidateRejected(RuntimeError):
    """Abort candidate publication while preserving the serving generation."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


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

    if not args.all and not args.promote:
        parser.error("--all is required unless --promote builds a snapshot from canonical products")
    if args.force and not args.all:
        parser.error("--force requires --all")

    # An explicit --start/--end (the --history=window --promote path) bounds
    # per-source materialization to that window instead of each source's
    # full history — see lynchpin-9rr. --history=all intentionally wants the
    # full history, so its window is resolved after the materialize pass,
    # once every source is fresh enough to report accurate date bounds.
    window: tuple[date, date] | None = None
    if args.promote and args.history == "window":
        if not args.start or not args.end:
            parser.error("--promote requires --start and --end unless --history all is used")
        window = (date.fromisoformat(args.start), date.fromisoformat(args.end))

    if args.all:
        _progress("planning canonical materialization")
        plan = plan_materializations(force=args.force, window=window)
    else:
        _progress("promoting from existing canonical products")
        plan = []
    _progress(f"plan ready: {len(plan)} step(s)")
    if args.plan_json:
        sys.stdout.write(json.dumps([step.to_json() for step in plan], indent=2, sort_keys=True) + "\n")
    for step in plan:
        _progress(f"{step.action}: {step.name} ({step.reason})")
    # Promotion has a stronger contract than ordinary product refreshes: every
    # writer sees a candidate database, then publication occurs only after the
    # snapshot has recorded a usable promoted generation. This leaves the last
    # serving generation queryable if a materializer or DuckDB fails.
    if args.promote:
        from lynchpin.substrate.connection import candidate_generation

        generation_context = candidate_generation()
    else:
        generation_context = nullcontext()

    try:
        with generation_context:
            run_materialization_plan(
                plan,
                window=window,
                continue_on_error=args.promote,
            )
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
                if not args.all:
                    forwarded.append("--existing-products")
                _progress(f"promoting substrate snapshot: {args.start}..{args.end}")
                code = snapshot_main(forwarded)
                if code:
                    raise _CandidateRejected(
                        code,
                        f"substrate snapshot failed with exit code {code}",
                    )
                _progress("substrate snapshot promotion complete")
            _progress("auditing materialization readiness")
            rows = audit_materialization()
            if args.json:
                sys.stdout.write(
                    json.dumps([row.to_json() for row in rows], indent=2, sort_keys=True)
                    + "\n"
                )
            if args.strict and any(row.status != "ready" for row in rows):
                raise _CandidateRejected(1, "strict materialization readiness check failed")
    except _CandidateRejected as exc:
        _progress(str(exc))
        return exc.code
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
