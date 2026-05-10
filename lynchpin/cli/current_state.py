"""Render a current-state context pack."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

from ..graph.context_pack import ContextPackMode, context_pack, render_context_pack
from ..graph.current_state_timeline import (
    build_current_state_timeline,
    render_current_state_timeline,
)
from ..core.parse import as_local
from ..core.serialization import jsonable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a current-state context pack")
    parser.add_argument("--start", type=_parse_date, required=True, help="Start logical date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, required=True, help="End logical date (YYYY-MM-DD)")
    parser.add_argument(
        "--github-frontier",
        action="store_true",
        help="Fetch open/recent GitHub issue and PR frontier through gh",
    )
    parser.add_argument(
        "--mode",
        choices=("local-fast", "local-heavy", "network"),
        default="local-fast",
        help="Context-pack cost mode; network implies GitHub frontier evidence",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Restrict context-pack project slices to a canonical project; repeatable",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Include deterministic semantic annotations, clusters, and narrative moments in the context pack",
    )
    parser.add_argument(
        "--persist-semantic",
        action="store_true",
        help="Persist deterministic semantic products to the local Lynchpin cache",
    )
    parser.add_argument("--json", action="store_true", help="Render structured JSON instead of Markdown")
    parser.add_argument("--output", "-o", type=Path, help="Write Markdown to this path instead of stdout")
    parser.add_argument(
        "--timeline-output",
        type=Path,
        help=(
            "Also write a chronological day-by-day timeline (M.10) to this path."
            " Sibling artifact of the context pack, citation-rich."
        ),
    )
    return parser


def render_current_state(
    *,
    start: date,
    end: date,
    include_github_frontier: bool = False,
    mode: ContextPackMode = "local-fast",
    projects: list[str] | None = None,
    semantic: bool = False,
    persist_semantic: bool = False,
    json_output: bool = False,
    timeline_output: Path | None = None,
) -> str:
    start_dt = as_local(datetime.combine(start, time.min))
    end_dt = as_local(datetime.combine(end, time.max))
    effective_mode: ContextPackMode = "network" if include_github_frontier else mode
    pack = context_pack(
        start=start_dt,
        end=end_dt,
        projects=projects,
        mode=effective_mode,
        semantic=semantic,
        persist_semantic=persist_semantic,
    )
    if timeline_output is not None:
        timeline = build_current_state_timeline(pack.graph, start=start, end=end)
        timeline_md = render_current_state_timeline(timeline)
        timeline_output.parent.mkdir(parents=True, exist_ok=True)
        timeline_output.write_text(timeline_md + "\n", encoding="utf-8")
    if json_output:
        return json.dumps(jsonable(pack), indent=2, sort_keys=True)
    return render_context_pack(pack)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must be on or after --start")

    rendered = render_current_state(
        start=args.start,
        end=args.end,
        include_github_frontier=args.github_frontier,
        mode=args.mode,
        projects=args.project,
        semantic=args.semantic,
        persist_semantic=args.persist_semantic,
        json_output=args.json,
        timeline_output=args.timeline_output,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value!r}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
