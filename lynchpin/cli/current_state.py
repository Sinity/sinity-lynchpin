"""Render a current-state context pack."""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import typer

from ..core.parse import as_local
from ..core.serialization import jsonable
from ..graph.context_pack import ContextPackSubstrateRequiredError

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
    projects: list[str] | None = None,
    weak_tags: bool = False,
    persist_weak_tags: bool = False,
    json_output: bool = False,
    timeline_output: Path | None = None,
    materialize_substrate: bool = False,
    progress: str = "plain",
) -> str:
    start_dt = as_local(datetime.combine(start, time.min))
    end_dt = as_local(datetime.combine(end, time.max))
    pack = context_pack(
        start=start_dt,
        end=end_dt,
        projects=projects,
        include_github_frontier=include_github_frontier,
        weak_tags=weak_tags,
        persist_weak_tags=persist_weak_tags,
        prefer_substrate=True,
        materialize_substrate=materialize_substrate,
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
    project: list[str] = typer.Option(
        None, "--project",
        help="Restrict context-pack project slices to a canonical project; repeatable",
    ),
    weak_tags: bool = typer.Option(
        False, "--weak-tags/",
        help="Include weak keyword/proximity evidence tags, clusters, and narrative moments in the context pack",
    ),
    persist_weak_tags: bool = typer.Option(
        False, "--persist-weak-tags/",
        help="Persist weak keyword/proximity evidence-tag products to the local Lynchpin cache",
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
    materialize_substrate: bool = typer.Option(
        False,
        "--materialize-substrate/",
        help="Materialize and replace the deterministic DuckDB graph for this range.",
    ),
    progress: str = typer.Option(
        "plain",
        "--progress",
        help="Progress output format: plain, json, or quiet.",
    ),
) -> None:
    if progress not in {"plain", "json", "quiet"}:
        raise typer.BadParameter("--progress must be one of: plain, json, quiet")
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        raise typer.BadParameter("--end must be on or after --start")

    rendered = render_current_state(
        start=start_d,
        end=end_d,
        include_github_frontier=github_frontier,
        projects=list(project or []),
        weak_tags=weak_tags,
        persist_weak_tags=persist_weak_tags,
        json_output=json_output,
        timeline_output=timeline_output,
        materialize_substrate=materialize_substrate,
        progress=progress,
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

    progress = _progress_arg(argv)
    _configure_progress_logging(progress)
    try:
        _command.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.UsageError as exc:
        sys.stderr.write(f"Error: {exc.format_message()}\n")
        return 2
    except ContextPackSubstrateRequiredError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except (typer.Exit, SystemExit) as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            code = getattr(exc, "code", 0)
        return int(code or 0)
    return 0


def _progress_arg(argv: list[str] | None) -> str:
    values = list(argv) if argv is not None else sys.argv[1:]
    if "--progress" not in values:
        return "plain"
    idx = values.index("--progress")
    return values[idx + 1] if idx + 1 < len(values) else "plain"


def _configure_progress_logging(progress: str) -> None:
    if progress == "quiet":
        logging.getLogger().setLevel(logging.WARNING)
        return
    root = logging.getLogger()
    if root.handlers:
        return
    if progress == "json":
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return json.dumps(
                    {
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "component": record.name,
                        "message": record.getMessage(),
                    },
                    sort_keys=True,
                )

        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
