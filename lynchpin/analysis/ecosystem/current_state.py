"""Active-project current-state materializer for the analysis package."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Sequence, cast

from ...graph.context_pack import ContextPackMode, context_pack, render_context_pack
from ...core.parse import as_local
from ...core.serialization import jsonable
from ..core.io import save_json, save_text

CURRENT_STATE_ARTIFACT_NAMES = (
    "current_state_context_pack.json",
    "current_state_context_pack.md",
    "current_state_narrative.json",
    "current_state_narrative.md",
)


def run_current_state_analysis(
    *,
    start: date,
    end: date,
    out_file: str | Path,
    markdown_out: str | Path | None = None,
    projects: Sequence[str] | None = None,
    mode: ContextPackMode = "local-fast",
    include_github_frontier: bool = False,
    semantic: bool = False,
    persist_semantic: bool = False,
) -> dict[str, Any]:
    """Materialize the graph-backed current-state context pack as analysis evidence."""
    effective_mode: ContextPackMode = "network" if include_github_frontier else mode
    start_dt = as_local(datetime.combine(start, time.min))
    end_dt = as_local(datetime.combine(end, time.max))
    pack = context_pack(
        start=start_dt,
        end=end_dt,
        projects=projects,
        mode=effective_mode,
        semantic=semantic,
        persist_semantic=persist_semantic,
        exclude_analysis_artifacts=CURRENT_STATE_ARTIFACT_NAMES,
        prefer_substrate=False,
    )
    payload = cast(dict[str, Any], jsonable(pack))
    payload["substrate_materialization"] = _promote_current_state_graph(
        pack.graph,
        start=start,
        end=end,
        mode=effective_mode,
        projects=projects,
    )
    save_json(out_file, payload, sort_keys=True)
    if markdown_out is not None:
        save_text(markdown_out, render_context_pack(pack) + "\n")
    return payload


def _promote_current_state_graph(
    graph: Any,
    *,
    start: date,
    end: date,
    mode: ContextPackMode,
    projects: Sequence[str] | None,
) -> dict[str, Any]:
    """Promote materialized current-state packs and return explicit status."""
    project_key = ",".join(sorted(projects or ())) if projects else "all"
    refresh_id = f"current-state:{start.isoformat()}:{end.isoformat()}:{mode}:{project_key}"
    try:
        from lynchpin.substrate import apply_schema, connect
        from lynchpin.substrate.graph import promote_evidence_graph
    except ImportError as exc:
        return {"status": "unavailable", "refresh_id": refresh_id, "reason": f"substrate import failed: {exc}"}
    try:
        with connect() as conn:
            apply_schema(conn)
            promote_evidence_graph(
                conn,
                refresh_id=refresh_id,
                graph=graph,
                projects=tuple(sorted(projects or ())),
            )
    except Exception as exc:
        return {"status": "failed", "refresh_id": refresh_id, "reason": str(exc)}
    return {"status": "promoted", "refresh_id": refresh_id, "reason": "stored in DuckDB substrate"}


__all__ = ["CURRENT_STATE_ARTIFACT_NAMES", "run_current_state_analysis"]
