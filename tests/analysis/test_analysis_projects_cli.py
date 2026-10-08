from __future__ import annotations

from typing import Any

import typer

from lynchpin.analysis.projects import cli as projects_cli


def _make_app() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)
    projects_cli.register_commands(app)
    return app


def _invoke(app: typer.Typer, args: list[str]) -> int:
    import click

    command = typer.main.get_command(app)
    try:
        command.main(args=args, standalone_mode=False)
    except click.UsageError as exc:
        raise AssertionError(f"unexpected usage error: {exc.format_message()}") from exc
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    return 0


def test_active_python_complexity_command_dispatches_materializer(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(out: str, **kwargs: object) -> None:
        seen["out"] = out
        seen["kwargs"] = kwargs

    monkeypatch.setattr(projects_cli, "run_active_python_complexity", fake_run)
    monkeypatch.setattr(projects_cli, "resolve_analysis_path", lambda name: f"/analysis/{name}")

    assert _invoke(
        _make_app(),
        [
            "active-python-complexity",
            "--start", "2026-05-01",
            "--end", "2026-05-02",
            "--project", "sinity-lynchpin",
            "--snapshot", "/tmp/snapshot.json",
            "--out", "/tmp/complexity.json",
        ],
    ) == 0
    from datetime import date

    assert seen == {
        "out": "/tmp/complexity.json",
        "kwargs": {
            "start": date(2026, 5, 1),
            "end": date(2026, 5, 2),
            "projects": ["sinity-lynchpin"],
            "snapshot_file": "/tmp/snapshot.json",
        },
    }


def test_active_python_import_graph_command_dispatches_materializer(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(out: str, **kwargs: object) -> None:
        seen["out"] = out
        seen["kwargs"] = kwargs

    monkeypatch.setattr(projects_cli, "run_active_python_import_graph", fake_run)
    monkeypatch.setattr(projects_cli, "resolve_analysis_path", lambda name: f"/analysis/{name}")

    assert _invoke(
        _make_app(),
        [
            "active-python-import-graph",
            "--project", "sinity-lynchpin",
        ],
    ) == 0
    assert seen == {
        "out": "/analysis/active_python_import_graph.json",
        "kwargs": {
            "projects": ["sinity-lynchpin"],
            "snapshot_file": "/analysis/active_project_snapshot.json",
        },
    }
