"""Materialize canonical Lynchpin products."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import inspect
import json
import logging
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..materialization import (
    audit_materialization,
    materializer_dependency_model,
    materializer_execution_waves,
    plan_materializations,
    run_materialization_plan,
)
from ..substrate.connection import (
    CandidateGeneration,
    CandidateGenerationInterrupted,
    CandidateGenerationRejected,
    bind_candidate_publication,
)
from ..substrate.run_steps import (
    PhaseMetric,
    PhaseMeasurement,
    log_phase_evidence,
    measure_phase,
    record_phase_evidence,
)

_PROGRESS_FORMAT = "plain"
log = logging.getLogger(__name__)


class _CandidateRejected(RuntimeError):
    """Abort candidate publication while preserving the serving generation."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical Lynchpin products")
    parser.add_argument("--all", action="store_true", help="materialize every locally rebuildable product")
    parser.add_argument("--promote", action="store_true", help="also build/promote a coherent substrate snapshot")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="initialize an empty substrate through one verified candidate publication",
    )
    parser.add_argument("--start", help="snapshot start date when --promote is used")
    parser.add_argument("--end", help="snapshot end date when --promote is used")
    parser.add_argument(
        "--history",
        choices=("window", "incremental", "all"),
        default="window",
        help="promote an explicit window, a bounded maintenance tail, or the full ready contract range",
    )
    parser.add_argument("--weak-tags", action="store_true", help="include weak keyword/proximity evidence tags in promoted snapshot")
    parser.add_argument("--snapshot-output", type=Path, default=None, help="write promoted snapshot Markdown to this path")
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any known product is not ready")
    parser.add_argument("--json", action="store_true", help="write JSON audit rows after materialization")
    parser.add_argument("--plan-json", action="store_true", help="write the materialization plan as JSON and exit without changing products")
    parser.add_argument("--force", action="store_true", help="rebuild all locally materializable products")
    parser.add_argument(
        "--rebuild-candidate-indexes",
        action="store_true",
        help="explicit audited recovery: copy verified logical rows into fresh DuckDB indexes",
    )
    parser.add_argument("--progress", choices=("plain", "json", "quiet"), default="plain")
    args = parser.parse_args(argv)
    global _PROGRESS_FORMAT
    _PROGRESS_FORMAT = args.progress

    if not args.all and not args.promote:
        parser.error("--all --promote is required unless --promote builds a snapshot from existing products")
    if args.all and not args.promote and not args.plan_json:
        parser.error("--all requires --promote so substrate writes use candidate publication")
    if args.bootstrap and not (args.all and args.promote):
        parser.error("--bootstrap requires --all --promote")
    if args.force and not args.all:
        parser.error("--force requires --all")
    if args.rebuild_candidate_indexes and not (args.all and args.promote):
        parser.error("--rebuild-candidate-indexes requires --all --promote")

    # An explicit --start/--end bounds every window-aware materializer to that
    # request. Incremental maintenance instead asks the planner for a separate
    # bounded tail for each product. Only --history=all permits unscoped
    # materializers, and it is retained exclusively for explicit repair/backfill.
    window: tuple[date, date] | None = None
    if args.promote and args.history == "window":
        if not args.start or not args.end:
            parser.error("--promote requires --start and --end unless --history all or incremental is used")
        window = (date.fromisoformat(args.start), date.fromisoformat(args.end))
    if args.history == "incremental" and not (args.all and args.promote):
        parser.error("--history incremental requires --all --promote")

    publication_refresh_id: str | None = None
    if args.promote:
        if args.history in {"all", "incremental"}:
            start_d, end_d = _all_history_window()
            args.start = start_d.isoformat()
            args.end = end_d.isoformat()
        elif not args.start or not args.end:
            parser.error("--promote requires --start and --end unless --history all or incremental is used")
        from .substrate_snapshot import _snapshot_refresh_id

        publication_refresh_id = _snapshot_refresh_id(
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            projects=(),
        )

    if args.all:
        _progress("planning canonical materialization")
        if args.history == "incremental":
            plan = plan_materializations(
                force=args.force,
                window=window,
                maintenance=True,
                maintenance_end=date.fromisoformat(args.end),
            )
        else:
            plan = plan_materializations(force=args.force, window=window)
    else:
        _progress("promoting from existing canonical products")
        plan = []
    _progress(f"plan ready: {len(plan)} step(s)")
    dependency_model = materializer_dependency_model(plan)
    writer_waves = materializer_execution_waves(dependency_model)
    if args.plan_json:
        sys.stdout.write(json.dumps([step.to_json() for step in plan], indent=2, sort_keys=True) + "\n")
        return 0
    for step in plan:
        _progress(f"{step.action}: {step.name} ({step.reason})")
    # Promotion has a stronger contract than ordinary product refreshes: every
    # writer sees a candidate database, then publication occurs only after the
    # snapshot has recorded a usable promoted generation. This leaves the last
    # serving generation queryable if a materializer or DuckDB fails.
    if args.promote:
        from lynchpin.substrate.connection import bootstrap_candidate_generation, candidate_generation

        if args.bootstrap:
            _progress("bootstrapping an empty substrate through candidate publication")
            generation_context = _candidate_context(
                bootstrap_candidate_generation,
                receipt_refresh_id=publication_refresh_id,
            )
        elif args.rebuild_candidate_indexes:
            _progress("rebuilding candidate indexes from verified logical rows")
            generation_context = _candidate_context(
                candidate_generation,
                rebuild_indexes=True,
                receipt_refresh_id=publication_refresh_id,
            )
        else:
            _progress("cloning verified candidate generation with copy-on-write extents")
            generation_context = _candidate_context(
                candidate_generation,
                receipt_refresh_id=publication_refresh_id,
            )
    else:
        generation_context = nullcontext()

    try:
        with generation_context as generation:
            # Independent source reads/computations share waves; canonical
            # product writes and receipt writes are serialized by their own
            # paths/locks. Graph construction remains a barrier after them.
            with _measure_incremental_phase("source_reads") as source_measurement:
                completed_steps = _run_materialization_plan(
                    plan,
                    refresh_id=publication_refresh_id,
                    window=window,
                    continue_on_error=args.promote,
                )
            _record_incremental_phase(
                generation,
                source_measurement,
                metrics=(
                    {"name": "completed_materializer_steps", "unit": "steps", "value": len(completed_steps)},
                    {"name": "materializer_writer_waves", "unit": "waves", "value": len(writer_waves)},
                ),
            )
            _progress("canonical materialization complete")
            if args.promote:
                incremental_tail_start: date | None = None
                if args.history == "incremental":
                    refreshed_windows = [
                        step.window
                        for step in plan
                        if step.action == "materialize" and step.window is not None
                    ]
                    incremental_tail_start = min(
                        (item[0] for item in refreshed_windows),
                        default=max(date.fromisoformat(args.start), date.fromisoformat(args.end) - timedelta(days=7)),
                    )
                    _progress(
                        "derived incremental graph tail: "
                        f"{incremental_tail_start.isoformat()}..{args.end}"
                    )
                elif args.history == "all":
                    _progress(f"derived all-history promotion window: {args.start}..{args.end}")
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
                if incremental_tail_start is not None:
                    forwarded.extend(("--incremental-tail-start", incremental_tail_start.isoformat()))
                if args.weak_tags:
                    forwarded.append("--weak-tags")
                if not args.all or args.history == "incremental":
                    forwarded.extend(("--existing-products", "--graph-only"))
                _progress(f"promoting substrate snapshot: {args.start}..{args.end}")
                with _measure_incremental_phase("graph_compute") as graph_measurement:
                    code = snapshot_main(forwarded)
                _record_incremental_phase(
                    generation,
                    graph_measurement,
                    metrics=({"name": "graph_promotions", "unit": "refreshes", "value": 1},),
                )
                if code:
                    raise _CandidateRejected(
                        code,
                        f"substrate snapshot failed with exit code {code}",
                    )
                if isinstance(generation, CandidateGeneration):
                    from .substrate_snapshot import _snapshot_refresh_id

                    bind_candidate_publication(
                        generation,
                        _snapshot_refresh_id(
                            start=date.fromisoformat(args.start),
                            end=date.fromisoformat(args.end),
                            projects=(),
                        ),
                    )
                _progress("substrate snapshot promotion complete")
            _progress("auditing materialization readiness")
            with _measure_incremental_phase("verification") as verification_measurement:
                rows = audit_materialization()
                if generation is not None:
                    from lynchpin.substrate.connection import generation_refresh_id

                    if generation_refresh_id(generation.candidate) is None:
                        raise _CandidateRejected(1, "candidate verification found no promoted generation")
            _record_incremental_phase(
                generation,
                verification_measurement,
                metrics=({"name": "audited_datasets", "unit": "datasets", "value": len(rows)},),
                evidence={
                    "audit_snapshot": [
                        {
                            "name": row.name,
                            "status": row.status,
                            "row_count": row.row_count,
                            "first_date": row.first_date.isoformat() if row.first_date else None,
                            "last_date": row.last_date.isoformat() if row.last_date else None,
                            "reason": row.reason,
                        }
                        for row in rows
                    ],
                    "candidate_refresh_id": getattr(generation, "refresh_id", None),
                    "candidate_seed_mode": getattr(generation, "seed_mode", None),
                    "publication_refresh_id": getattr(generation, "expected_refresh_id", None),
                },
            )
            if args.json:
                sys.stdout.write(
                    json.dumps([row.to_json() for row in rows], indent=2, sort_keys=True)
                    + "\n"
                )
            if args.strict and any(row.status != "ready" for row in rows):
                raise _CandidateRejected(1, "strict materialization readiness check failed")
    except CandidateGenerationInterrupted as exc:
        signal_name = signal.Signals(exc.signal_number).name
        _progress(f"promoted materialization interrupted by {signal_name}; candidate was archived")
        return 128 + exc.signal_number
    except CandidateGenerationRejected as exc:
        _progress(f"candidate materialization rejected: {exc}")
        return 1
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


def _candidate_context(factory: Any, **kwargs: Any) -> Any:
    """Call candidate factories while keeping narrow test/dry-run shims compatible."""
    parameters = inspect.signature(factory).parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return factory(**kwargs)
    return factory(**{key: value for key, value in kwargs.items() if key in parameters})


def _run_materialization_plan(plan: Any, **kwargs: Any) -> Any:
    parameters = inspect.signature(run_materialization_plan).parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return run_materialization_plan(plan, **kwargs)
    return run_materialization_plan(
        plan,
        **{key: value for key, value in kwargs.items() if key in parameters},
    )


def _measure_incremental_phase(phase: str) -> PhaseMeasurement:
    return measure_phase(phase)


def _record_incremental_phase(
    generation: CandidateGeneration | None,
    measurement: PhaseMeasurement,
    *,
    metrics: tuple[PhaseMetric, ...],
    evidence: dict[str, object] | None = None,
) -> None:
    """Append phase evidence only when this invocation owns a candidate."""
    if generation is None:
        return
    from lynchpin.substrate.connection import connect
    expected_refresh_id = getattr(generation, "expected_refresh_id", None)
    receipt_refresh_id_value = (
        expected_refresh_id
        or getattr(generation, "receipt_refresh_id", None)
        or getattr(generation, "refresh_id", None)
        or "unknown"
    )
    receipt_refresh_id: str = str(receipt_refresh_id_value)
    try:
        with connect(generation.candidate) as conn:
            payload = record_phase_evidence(
                conn,
                refresh_id=receipt_refresh_id,
                measurement=measurement,
                metrics=metrics,
                evidence=evidence,
            )
            conn.execute("CHECKPOINT")
        payload["refresh_id"] = receipt_refresh_id
    except Exception as exc:  # noqa: BLE001 - receipt loss must not hide completed work.
        payload = measurement.payload(metrics=metrics, evidence=evidence)
        payload["refresh_id"] = receipt_refresh_id
        payload["receipt_status"] = "degraded"
        payload["receipt_error"] = f"{type(exc).__name__}: {exc}"
        log.warning(
            "incremental phase receipt write failed: phase=%s status=degraded error=%s",
            measurement.phase,
            exc,
        )
    generation.phase_evidence.append(payload)
    log_phase_evidence(measurement, metrics=metrics, evidence=evidence)


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
