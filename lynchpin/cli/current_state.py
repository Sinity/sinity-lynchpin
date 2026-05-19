"""Render a current-state context pack."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import typer

from ..core.parse import as_local
from ..core.serialization import jsonable

ContextPackMode = Literal["local-fast", "local-heavy", "network"]


def context_pack(*args: Any, **kwargs: Any) -> Any:
    from ..graph.context_pack import context_pack as impl

    return impl(*args, **kwargs)


def render_context_pack(*args: Any, **kwargs: Any) -> str:
    from ..graph.context_pack import render_context_pack as impl

    return impl(*args, **kwargs)


def build_current_state_timeline(*args: Any, **kwargs: Any) -> Any:
    from ..graph.current_state_timeline import build_current_state_timeline as impl

    return impl(*args, **kwargs)


def render_current_state_timeline(*args: Any, **kwargs: Any) -> str:
    from ..graph.current_state_timeline import render_current_state_timeline as impl

    return impl(*args, **kwargs)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid date: {value!r}") from exc


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
        prefer_substrate=True,
    )
    if timeline_output is not None:
        timeline = build_current_state_timeline(pack.graph, start=start, end=end)
        timeline_md = render_current_state_timeline(timeline)
        timeline_output.parent.mkdir(parents=True, exist_ok=True)
        timeline_output.write_text(timeline_md + "\n", encoding="utf-8")
    if json_output:
        return json.dumps(jsonable(pack), indent=2, sort_keys=True)
    return render_context_pack(pack)


def _current_state_command(
    start: str = typer.Option(..., "--start", help="Start logical date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End logical date (YYYY-MM-DD)"),
    github_frontier: bool = typer.Option(
        False, "--github-frontier/",
        help="Fetch open/recent GitHub issue and PR frontier through gh",
    ),
    mode: str = typer.Option(
        "local-fast", "--mode",
        help="Context-pack cost mode; network implies GitHub frontier evidence",
    ),
    project: list[str] = typer.Option(
        None, "--project",
        help="Restrict context-pack project slices to a canonical project; repeatable",
    ),
    semantic: bool = typer.Option(
        False, "--semantic/",
        help="Include deterministic semantic annotations, clusters, and narrative moments in the context pack",
    ),
    persist_semantic: bool = typer.Option(
        False, "--persist-semantic/",
        help="Persist deterministic semantic products to the local Lynchpin cache",
    ),
    json_output: bool = typer.Option(False, "--json/", help="Render structured JSON instead of Markdown"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write Markdown to this path instead of stdout"),
    timeline_output: Path | None = typer.Option(
        None, "--timeline-output",
        help=(
            "Also write a chronological day-by-day timeline (M.10) to this path."
            " Sibling artifact of the context pack, citation-rich."
        ),
    ),
) -> None:
    if mode not in {"local-fast", "local-heavy", "network"}:
        raise typer.BadParameter(
            f"argument --mode: invalid choice: {mode!r} (choose from 'local-fast', 'local-heavy', 'network')"
        )
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        raise typer.BadParameter("--end must be on or after --start")

    rendered = render_current_state(
        start=start_d,
        end=end_d,
        include_github_frontier=github_frontier,
        mode=mode,  # type: ignore[arg-type]
        projects=list(project or []),
        semantic=semantic,
        persist_semantic=persist_semantic,
        json_output=json_output,
        timeline_output=timeline_output,
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")


_app = typer.Typer(
    help="Render a current-state context pack",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_app.command()(_current_state_command)
_command = typer.main.get_command(_app)


def main(argv: list[str] | None = None) -> int:
    import click

    try:
        _command.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.UsageError as exc:
        sys.stderr.write(f"Error: {exc.format_message()}\n")
        return 2
    except (typer.Exit, SystemExit) as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            code = getattr(exc, "code", 0)
        return int(code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
