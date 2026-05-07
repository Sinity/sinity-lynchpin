"""Active-project current-state materializer for the analysis package."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Sequence, cast

from ...composite.context_pack import ContextPackMode, context_pack, render_context_pack
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
    )
    payload = cast(dict[str, Any], jsonable(pack))
    save_json(out_file, payload, sort_keys=True)
    if markdown_out is not None:
        save_text(markdown_out, render_context_pack(pack) + "\n")
    return payload


__all__ = ["CURRENT_STATE_ARTIFACT_NAMES", "run_current_state_analysis"]
