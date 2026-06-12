"""Polylogue session time-composition analysis artifact."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.sources.polylogue import archive_readiness
from lynchpin.sources.polylogue_timeline import session_composition, session_compositions


def build_polylogue_time_composition(
    *,
    start: date,
    end: date,
    session_id: str | None = None,
    limit: int | None = 50,
    include_cross_source: bool = True,
) -> dict[str, Any]:
    readiness = archive_readiness()
    if session_id:
        rows = [
            session_composition(
                session_id,
                include_cross_source=include_cross_source,
            )
        ]
    else:
        rows = session_compositions(
            start=start,
            end=end,
            limit=limit,
            include_cross_source=include_cross_source,
        )
    total_wall = sum(row.wall_seconds for row in rows)
    total_engaged = sum(row.engaged_seconds for row in rows)
    by_kind: dict[str, float] = {}
    by_lane: dict[str, float] = {}
    cross: dict[str, float] = {}
    for row in rows:
        for key, value in row.seconds_by_kind.items():
            by_kind[key] = by_kind.get(key, 0.0) + value
        for key, value in row.seconds_by_lane.items():
            by_lane[key] = by_lane.get(key, 0.0) + value
        for key, value in row.cross_source_seconds.items():
            cross[key] = cross.get(key, 0.0) + value
    return {
        "kind": "polylogue_time_composition",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "readiness": {
            "status": readiness.status,
            "reason": readiness.reason,
            "db_path": str(readiness.db_path),
            "session_profile_count": readiness.session_profile_count,
            "work_event_count": readiness.work_event_count,
        },
        "scope": {
            "session_id": session_id,
            "limit": limit,
            "include_cross_source": include_cross_source,
        },
        "summary": {
            "session_count": len(rows),
            "ok_sessions": sum(1 for row in rows if row.status == "ok"),
            "wall_seconds": round(total_wall, 3),
            "engaged_seconds": round(total_engaged, 3),
            "seconds_by_lane": _rounded(by_lane),
            "seconds_by_kind": _rounded(by_kind),
            "cross_source_seconds": _rounded(cross),
        },
        "sessions": [_row_to_dict(row) for row in rows],
    }


def run_polylogue_time_composition(
    out_file: str | Path,
    *,
    start: date,
    end: date,
    markdown_out: str | Path | None = None,
    session_id: str | None = None,
    limit: int | None = 50,
    include_cross_source: bool = True,
) -> dict[str, Any]:
    payload = build_polylogue_time_composition(
        start=start,
        end=end,
        session_id=session_id,
        limit=limit,
        include_cross_source=include_cross_source,
    )
    save_json(out_file, payload)
    if markdown_out is not None:
        Path(markdown_out).parent.mkdir(parents=True, exist_ok=True)
        Path(markdown_out).write_text(render_markdown(payload), encoding="utf-8")
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Polylogue Time Composition",
        "",
        f"Window: {payload['window']['start']} to {payload['window']['end']}",
        f"Readiness: {payload['readiness']['status']} - {payload['readiness']['reason']}",
        "",
        "## Summary",
        "",
        f"- Sessions: {summary['session_count']} ({summary['ok_sessions']} ok)",
        f"- Wall hours: {summary['wall_seconds'] / 3600:.2f}",
        f"- Engaged hours: {summary['engaged_seconds'] / 3600:.2f}",
        "",
        "## Seconds By Lane",
        "",
    ]
    for key, value in summary["seconds_by_lane"].items():
        lines.append(f"- {key}: {value:.1f}")
    lines.extend(["", "## Example Sessions", ""])
    for row in payload["sessions"][:10]:
        lines.append(f"### {row['session_id']}")
        lines.append("")
        lines.append(f"- Provider: {row['provider']}")
        lines.append(f"- Status: {row['status']}")
        lines.append(f"- Wall seconds: {row['wall_seconds']:.1f}")
        lines.append(f"- Lanes: `{json.dumps(row['seconds_by_lane'], sort_keys=True)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "provider": row.provider,
        "title": row.title,
        "start": row.start.isoformat() if row.start else None,
        "end": row.end.isoformat() if row.end else None,
        "status": row.status,
        "reason": row.reason,
        "message_count": row.message_count,
        "wall_seconds": row.wall_seconds,
        "engaged_seconds": row.engaged_seconds,
        "span_count": row.span_count,
        "overlap_count": row.overlap_count,
        "seconds_by_lane": row.seconds_by_lane,
        "seconds_by_kind": row.seconds_by_kind,
        "cross_source_seconds": row.cross_source_seconds,
        "projects": list(row.projects),
        "tags": list(row.tags),
    }


def _rounded(rows: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 3) for key, value in sorted(rows.items())}
