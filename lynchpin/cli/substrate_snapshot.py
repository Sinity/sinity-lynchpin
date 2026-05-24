"""Build one coherent materialized substrate snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .current_state import main as current_state_main

_PROGRESS_FORMAT = "plain"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a coherent Lynchpin substrate snapshot")
    parser.add_argument("--start", required=True, help="inclusive start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="exclusive end date, YYYY-MM-DD")
    parser.add_argument("--weak-tags", action="store_true", help="include weak keyword/proximity evidence tags")
    parser.add_argument("--project", action="append", dest="projects")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--timeline-output", type=Path, default=None)
    parser.add_argument("--progress", choices=("plain", "json", "quiet"), default="plain")
    args = parser.parse_args(argv)
    global _PROGRESS_FORMAT
    _PROGRESS_FORMAT = args.progress

    # Validate dates here so errors point at this stable public entrypoint.
    date.fromisoformat(args.start)
    date.fromisoformat(args.end)
    _progress(f"building current-state graph: {args.start}..{args.end}")
    refresh_id = _snapshot_refresh_id(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        projects=tuple(args.projects or ()),
    )
    _record_run_step(refresh_id, "current_state_graph", "started", "building current-state graph")

    forwarded = [
        "--start",
        args.start,
        "--end",
        args.end,
        "--refresh-substrate",
        "--progress",
        args.progress,
    ]
    if args.weak_tags:
        forwarded.append("--weak-tags")
    for project in args.projects or ():
        forwarded.extend(["--project", project])
    if args.output is not None:
        forwarded.extend(["--output", str(args.output)])
    if args.timeline_output is not None:
        forwarded.extend(["--timeline-output", str(args.timeline_output)])
    code = current_state_main(forwarded)
    if code:
        _record_run_step(refresh_id, "current_state_graph", "error", f"current-state exited {code}")
        return code
    _record_run_step(refresh_id, "current_state_graph", "ok", "current-state graph materialized")

    _progress("recording dataset readiness statuses")
    _record_run_step(refresh_id, "dataset_readiness", "started", "recording dataset readiness statuses")
    _record_snapshot_materialization_statuses(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        projects=tuple(args.projects or ()),
    )
    _record_run_step(refresh_id, "dataset_readiness", "ok", "dataset readiness statuses recorded")
    _progress("promoting daily personal-signal rows")
    _record_run_step(refresh_id, "personal_daily_signal", "started", "promoting daily personal/content rows")
    _promote_snapshot_daily_signals(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        projects=tuple(args.projects or ()),
    )
    _record_run_step(refresh_id, "personal_daily_signal", "ok", "daily personal/content rows promoted")
    _progress("recording promotion run")
    _record_run_step(refresh_id, "promotion_run", "started", "recording promotion run")
    _record_snapshot_promotion_run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        projects=tuple(args.projects or ()),
    )
    _record_run_step(refresh_id, "promotion_run", "ok", "promotion run recorded")
    _progress("snapshot promotion complete")
    return 0


def _progress(message: str) -> None:
    if _PROGRESS_FORMAT == "quiet":
        return
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    if _PROGRESS_FORMAT == "json":
        sys.stderr.write(json.dumps({"ts": stamp, "component": "substrate-snapshot", "message": message}, sort_keys=True) + "\n")
    else:
        sys.stderr.write(f"[{stamp}] substrate-snapshot: {message}\n")
    sys.stderr.flush()


def _record_run_step(
    refresh_id: str,
    step: str,
    status: str,
    message: str | None = None,
    row_count: int | None = None,
) -> None:
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path
    from lynchpin.substrate.run_steps import record_run_step

    try:
        with connect(substrate_path()) as conn:
            apply_schema(conn)
            record_run_step(
                conn,
                refresh_id=refresh_id,
                step=step,
                status=status,
                message=message,
                row_count=row_count,
            )
    except Exception as exc:
        _progress(f"run-step observability skipped for {step}: {exc}")


def _snapshot_refresh_id(
    *,
    start: date,
    end: date,
    projects: tuple[str, ...],
) -> str:
    from lynchpin.graph.context_pack import _current_state_refresh_id

    return _current_state_refresh_id(
        start=start,
        end=end,
        projects=projects,
    )


def _record_snapshot_materialization_statuses(
    *,
    start: date,
    end: date,
    projects: tuple[str, ...],
) -> None:
    from lynchpin.analysis.active.substrate_promote_status import record_source_status
    from lynchpin.analysis.active.substrate_promote_status import SOURCE_EVIDENCE_GRAPH
    from lynchpin.core.source_contracts import dataset_status_to_substrate_status
    from lynchpin.materialization import audit_materialization
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    refresh_id = _snapshot_refresh_id(
        start=start,
        end=end,
        projects=projects,
    )
    audit_rows = audit_materialization()
    with connect(substrate_path()) as conn:
        apply_schema(conn)
        for row in audit_rows:
            status = dataset_status_to_substrate_status(row.status)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=row.name,
                kind="dataset",
                status=status,
                reason=None if status == "ok" else row.reason,
                row_count=row.row_count or 0,
                window_start=start,
                window_end=end,
            )
        graph_row = conn.execute(
            "SELECT node_count FROM evidence_graph_build WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_EVIDENCE_GRAPH,
            status="ok" if graph_row else "error",
            reason=None if graph_row else "evidence graph build row is missing after snapshot promotion",
            row_count=int(graph_row[0]) if graph_row else 0,
            window_start=start,
            window_end=end,
        )


def _promote_snapshot_daily_signals(
    *,
    start: date,
    end: date,
    projects: tuple[str, ...],
) -> None:
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_PERSONAL_DAILY_SIGNAL,
        record_source_status,
    )
    from lynchpin.sources.activity_content import iter_activity_content_days, iter_activity_title_usage
    from lynchpin.sources.personal_signals import iter_personal_daily_signals
    from lynchpin.sources.title_metadata import title_metadata_path
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path
    from lynchpin.substrate.personal import (
        promote_activity_content_buckets,
        promote_activity_content_days,
        promote_activity_title_usage,
        promote_personal_daily_signals,
        promote_title_classifications_from_path,
    )

    refresh_id = _snapshot_refresh_id(start=start, end=end, projects=projects)
    rows = [
        (row.source, row.date, row.metric, row.value, row.dimensions)
        for row in iter_personal_daily_signals()
        if start <= row.date < end
    ]
    with connect(substrate_path()) as conn:
        apply_schema(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            title_count = promote_title_classifications_from_path(
                conn,
                refresh_id=refresh_id,
                path=str(title_metadata_path()),
            )
            content_rows = [
                row
                for row in iter_activity_content_days()
                if start <= row.date < end
            ]
            content_count = promote_activity_content_days(
                conn,
                refresh_id=refresh_id,
                rows=content_rows,
            )
            bucket_count = promote_activity_content_buckets(
                conn,
                refresh_id=refresh_id,
                rows=content_rows,
            )
            usage_count = promote_activity_title_usage(
                conn,
                refresh_id=refresh_id,
                rows=(
                    row
                    for row in iter_activity_title_usage()
                    if row.last_date is not None
                    and row.first_date is not None
                    and row.first_date < end
                    and row.last_date >= start
                ),
            )
            count = promote_personal_daily_signals(conn, refresh_id=refresh_id, rows=rows)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source="title_classification",
                status="ok" if title_count else "empty",
                reason=None if title_count else "no title classifications available",
                row_count=title_count,
                window_start=start,
                window_end=end,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source="activity_content",
                status="ok" if content_count else "empty",
                reason=None if content_count else "no activity-content rows in window",
                row_count=content_count + bucket_count + usage_count,
                window_start=start,
                window_end=end,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_PERSONAL_DAILY_SIGNAL,
                status="ok" if count else "empty",
                reason=None if count else "no daily personal-source signals in window",
                row_count=count,
                window_start=start,
                window_end=end,
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _record_snapshot_promotion_run(
    *,
    start: date,
    end: date,
    projects: tuple[str, ...],
) -> None:
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    refresh_id = _snapshot_refresh_id(start=start, end=end, projects=projects)
    with connect(substrate_path()) as conn:
        apply_schema(conn)
        status_rows = conn.execute(
            """
            SELECT source, status, reason
            FROM substrate_source_status
            WHERE refresh_id = ?
            ORDER BY source
            """,
            [refresh_id],
        ).fetchall()
        graph_row = conn.execute(
            "SELECT node_count, edge_count FROM evidence_graph_build WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()
        counts = {
            "evidence_graph_nodes": int(graph_row[0]) if graph_row else 0,
            "evidence_graph_edges": int(graph_row[1]) if graph_row else 0,
            "analysis_claims": int(
                conn.execute(
                    "SELECT COUNT(*) FROM analysis_claim WHERE refresh_id = ?",
                    [refresh_id],
                ).fetchone()[0]
            ),
            "personal_daily_signal": int(
                conn.execute(
                    "SELECT COUNT(*) FROM personal_daily_signal WHERE refresh_id = ?",
                    [refresh_id],
                ).fetchone()[0]
            ),
        }
        status = "ok"
        reason = None
        bad = [row for row in status_rows if row[1] in {"error", "unavailable"}]
        if bad:
            status = "error" if any(row[1] == "error" for row in bad) else "degraded"
            reason = "; ".join(f"{row[0]}: {row[2] or row[1]}" for row in bad[:6])
        conn.execute("DELETE FROM substrate_promotion_run WHERE refresh_id = ?", [refresh_id])
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, now(), now())
            """,
            [
                refresh_id,
                status,
                reason,
                start,
                end,
                "materialized",
                json.dumps(counts, sort_keys=True),
            ],
        )


if __name__ == "__main__":
    raise SystemExit(main())
