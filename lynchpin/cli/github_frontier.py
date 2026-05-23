"""Render GitHub issue/PR frontier in per-project batches."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from ..core.serialization import jsonable


def active_project_inventory(*args: Any, **kwargs: Any) -> Any:
    from ..graph.current_state import active_project_inventory as impl

    return impl(*args, **kwargs)


def project_inventory(*args: Any, **kwargs: Any) -> Any:
    from ..graph.current_state import project_inventory as impl

    return impl(*args, **kwargs)


def project_github_frontier(*args: Any, **kwargs: Any) -> Any:
    from ..graph.current_state import project_github_frontier as impl

    return impl(*args, **kwargs)


def github_frontier_markdown(*args: Any, **kwargs: Any) -> str:
    from ..graph.current_state import github_frontier_markdown as impl

    return impl(*args, **kwargs)


def github_frontier_summary_markdown(*args: Any, **kwargs: Any) -> str:
    from ..graph.current_state import github_frontier_summary_markdown as impl

    return impl(*args, **kwargs)


def render_github_frontier_batch(
    *,
    projects: list[str] | None = None,
    json_output: bool = False,
) -> str:
    inventory = tuple(project_inventory()) if projects else tuple(active_project_inventory())
    selected = set(projects or ())
    if selected:
        inventory = tuple(item for item in inventory if item.name in selected)
    frontiers = []
    for item in inventory:
        frontiers.extend(project_github_frontier((item,)))
    if json_output:
        return json.dumps(jsonable(frontiers), indent=2, sort_keys=True)
    return "\n\n".join(
        (
            "# GitHub Frontier",
            "",
            github_frontier_summary_markdown(frontiers),
            "",
            github_frontier_markdown(frontiers),
        )
    ).rstrip()


def _github_frontier_command(
    project: list[str] = typer.Option(
        None,
        "--project",
        help="Fetch only this project; repeatable. Without it, active projects are batched one project at a time.",
    ),
    json_output: bool = typer.Option(False, "--json/", help="Render structured JSON instead of Markdown"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write output to this path instead of stdout"),
) -> None:
    rendered = render_github_frontier_batch(projects=list(project or []), json_output=json_output)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")


_app = typer.Typer(
    help="Render GitHub frontier in per-project batches",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_app.command()(_github_frontier_command)
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
