"""Evidence-bundle builders for retrospective periods."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..periods import Period, parse_period
from .history import relative_bundle_ref, write_evidence_bundle
from .trust import SurfaceFreshness, inspect_core_surface_freshness, open_warehouse_read_only, render_surface_freshness_markdown

if TYPE_CHECKING:
    import duckdb


@dataclass(frozen=True)
class EvidenceQuery:
    query_id: str
    title: str
    sql: str
    params: list[Any]
    rows: list[dict[str, Any]]
    error: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "title": self.title,
            "sql": self.sql,
            "params": [_json_default(value) for value in self.params],
            "row_count": self.row_count,
            "rows": self.rows,
            "error": self.error,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    period: Period
    generated_at: str
    freshness: list[SurfaceFreshness]
    queries: list[EvidenceQuery]
    notes: list[str]
    bundle_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.to_dict(),
            "generated_at": self.generated_at,
            "freshness": [row.to_dict() for row in self.freshness],
            "queries": [query.to_dict() for query in self.queries],
            "notes": self.notes,
            "bundle_ref": self.bundle_ref,
        }


def build_period_evidence_bundle(
    scale: Any,
    key: str,
    *,
    write: bool = False,
) -> EvidenceBundle:
    period = parse_period(scale, key)
    if period is None:
        raise ValueError(f"Unsupported narrative period: {scale!r} {key!r}")

    conn = open_warehouse_read_only()
    try:
        freshness = inspect_core_surface_freshness(conn=conn, reference_date=period.end)
        queries = query_evidence_range(
            conn,
            start=period.start,
            end=period.end,
            artifact_limits=True,
        )
    finally:
        conn.close()

    bundle = EvidenceBundle(
        period=period,
        generated_at=datetime.now(UTC).isoformat(),
        freshness=freshness,
        queries=queries,
        notes=[
            "Bundle built from stable warehouse tables and direct session-profile surfaces.",
            "Queries prefer processed evidence planes and direct session-profile surfaces over aggregate trajectory views.",
        ],
        bundle_ref=None,
    )

    if not write:
        return bundle

    summary = render_period_evidence_markdown(bundle)
    bundle_dir = write_evidence_bundle(
        scale=period.scale,
        key=period.key,
        bundle_payload=bundle.to_dict(),
        query_payloads=[query.to_dict() for query in queries],
        summary_markdown=summary,
    )
    return EvidenceBundle(
        period=bundle.period,
        generated_at=bundle.generated_at,
        freshness=bundle.freshness,
        queries=bundle.queries,
        notes=bundle.notes,
        bundle_ref=relative_bundle_ref(bundle_dir),
    )


def render_period_evidence_markdown(bundle: EvidenceBundle) -> str:
    lines = [
        f"# Evidence Bundle — {bundle.period.scale}:{bundle.period.key}",
        "",
        f"- Generated at: {bundle.generated_at}",
        f"- Range: {bundle.period.start.isoformat()} → {bundle.period.end.isoformat()}",
        "",
        "## Freshness",
        "",
        render_surface_freshness_markdown(bundle.freshness) or "- n/a",
    ]

    for query in bundle.queries:
        lines.extend(
            [
                "",
                f"## {query.title}",
                "",
                f"- Query id: `{query.query_id}`",
                f"- Rows: {query.row_count}",
            ],
        )
        if query.error:
            lines.append(f"- Error: {query.error}")
            continue
        if not query.rows:
            lines.append("- No rows returned.")
            continue
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(query.rows[:8], ensure_ascii=False, indent=2, default=_json_default))
        lines.append("```")

    return "\n".join(lines).rstrip() + "\n"


def query_evidence_range(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date,
    end: date,
    artifact_limits: bool = True,
) -> list[EvidenceQuery]:
    date_params = [start, end]
    git_file_limit = "LIMIT 200" if artifact_limits else ""
    focus_span_limit = "LIMIT 120" if artifact_limits else ""
    focus_loop_limit = "LIMIT 80" if artifact_limits else ""
    profile_limit = "LIMIT 120" if artifact_limits else ""
    queries = [
        (
            "delivery_telemetry",
            "Delivery Telemetry",
            """
            SELECT *
            FROM processed_delivery_telemetry
            WHERE date BETWEEN ? AND ?
            ORDER BY date
            """,
            date_params,
        ),
        (
            "project_attention",
            "Project Attention",
            """
            SELECT *
            FROM processed_project_attention
            WHERE date BETWEEN ? AND ?
            ORDER BY date
            """,
            date_params,
        ),
        (
            "chat_activity",
            "Chat Activity",
            """
            SELECT *
            FROM processed_chat_activity
            WHERE date BETWEEN ? AND ?
            ORDER BY date, provider
            """,
            date_params,
        ),
        (
            "git_daily",
            "Git Daily",
            """
            SELECT *
            FROM processed_git_daily
            WHERE date BETWEEN ? AND ?
            ORDER BY date, commit_count DESC, repo
            """,
            date_params,
        ),
        (
            "git_file_facts",
            "Git File Facts",
            """
            SELECT date, repo, authored_at, commit_sha, path, path_root, lines_added, lines_deleted, lines_changed
            FROM processed_git_file_facts
            WHERE date BETWEEN ? AND ?
            ORDER BY date, lines_changed DESC, repo, path
            {git_file_limit}
            """,
            date_params,
        ),
        (
            "focus_spans",
            "Focus Spans",
            """
            SELECT date, start, end_time, span_kind, app, title, mode, project, duration_seconds, keypress_count, keylog_state
            FROM processed_focus_spans
            WHERE date BETWEEN ? AND ?
            ORDER BY duration_seconds DESC, start
            {focus_span_limit}
            """,
            date_params,
        ),
        (
            "focus_loops",
            "Focus Loops",
            """
            SELECT *
            FROM processed_focus_loops
            WHERE date BETWEEN ? AND ?
            ORDER BY duration_minutes DESC, start
            {focus_loop_limit}
            """,
            date_params,
        ),
        (
            "context_switches",
            "Context Switches",
            """
            SELECT *
            FROM processed_context_switches
            WHERE date BETWEEN ? AND ?
            ORDER BY date
            """,
            date_params,
        ),
        (
            "circadian",
            "Circadian Profile",
            """
            SELECT *
            FROM processed_circadian
            WHERE date BETWEEN ? AND ?
            ORDER BY date, hour
            """,
            date_params,
        ),
        (
            "deep_work",
            "Deep Work",
            """
            SELECT *
            FROM processed_deep_work
            WHERE date BETWEEN ? AND ?
            ORDER BY date, duration_minutes DESC, start
            """,
            date_params,
        ),
        (
            "polylogue_sessions",
            "Polylogue Session Profiles",
            """
            SELECT provider, conversation_id, title, created_at, first_message_at, last_message_at,
                   message_count, substantive_count, work_event_count, dominant_work_kind,
                   cost_usd, continuation_depth, thread_id,
                   canonical_projects_json, auto_tags_json
            FROM polylogue_session_profile
            WHERE CAST(COALESCE(last_message_at, created_at) AS DATE) BETWEEN ? AND ?
            ORDER BY COALESCE(last_message_at, created_at)
            {profile_limit}
            """,
            date_params,
        ),
    ]

    return [
        _run_query(conn, query_id, title, sql.format(
            git_file_limit=git_file_limit,
            focus_span_limit=focus_span_limit,
            focus_loop_limit=focus_loop_limit,
            profile_limit=profile_limit,
        ), params)
        for query_id, title, sql, params in queries
    ]


def _run_query(
    conn: "duckdb.DuckDBPyConnection",
    query_id: str,
    title: str,
    sql: str,
    params: list[Any],
) -> EvidenceQuery:
    try:
        rows = conn.execute(sql, params).fetchall()
        columns = [meta[0] for meta in conn.description]
        payload = [
            {name: _json_default(value) for name, value in zip(columns, row)}
            for row in rows
        ]
        return EvidenceQuery(query_id=query_id, title=title, sql=_clean_sql(sql), params=params, rows=payload)
    except Exception as exc:
        return EvidenceQuery(
            query_id=query_id,
            title=title,
            sql=_clean_sql(sql),
            params=params,
            rows=[],
            error=str(exc),
        )


def _clean_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.strip().splitlines())


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def cli() -> None:
    parser = argparse.ArgumentParser(description="Build a Lynchpin evidence bundle for a narrative period.")
    parser.add_argument("--scale", default="day", help="Narrative scale: day, week, month, quarter, half, year.")
    parser.add_argument("--key", help="Period key, e.g. 2026-03-16, 2026-W12, 2026-03, 2026-Q1, 2026-H1, 2026.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional standalone JSON output path for the bundle payload.",
    )
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON packet to stdout.")
    parser.add_argument(
        "--write-artifacts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Control whether colocated bundle artifacts are materialized under artefacts/retrospective/narratives.",
    )
    args = parser.parse_args()

    scale = str(args.scale).strip().lower()
    key = args.key or _default_key(scale)
    period = parse_period(scale, key)
    if period is None:
        raise SystemExit(f"Unsupported period: scale={scale!r} key={key!r}")

    bundle = build_period_evidence_bundle(scale, key, write=args.write_artifacts)
    payload = bundle.to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True, default=_json_default)

    if args.output is not None:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {output_path}")
    elif bundle.bundle_ref:
        print(f"Wrote {bundle.bundle_ref}")

    if args.stdout:
        print(text)


def _default_key(scale: str) -> str:
    now = datetime.now(UTC)
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
