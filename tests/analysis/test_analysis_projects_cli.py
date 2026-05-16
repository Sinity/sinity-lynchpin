from __future__ import annotations

import argparse
from typing import Any

from lynchpin.analysis.projects import cli as projects_cli


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    projects_cli.add_analysis_commands(subparsers)
    return parser.parse_args(argv)


def test_active_python_complexity_command_dispatches_materializer(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(out: str, **kwargs: object) -> None:
        seen["out"] = out
        seen["kwargs"] = kwargs

    monkeypatch.setattr(projects_cli, "run_active_python_complexity", fake_run)
    monkeypatch.setattr(projects_cli, "resolve_analysis_path", lambda name: f"/analysis/{name}")

    args = _parse([
        "active-python-complexity",
        "--start", "2026-05-01",
        "--end", "2026-05-02",
        "--project", "sinity-lynchpin",
        "--snapshot", "/tmp/snapshot.json",
        "--out", "/tmp/complexity.json",
    ])

    assert projects_cli.run_analysis_command(args) == 0
    assert seen == {
        "out": "/tmp/complexity.json",
        "kwargs": {
            "start": args.start,
            "end": args.end,
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

    args = _parse([
        "active-python-import-graph",
        "--project", "sinity-lynchpin",
    ])

    assert projects_cli.run_analysis_command(args) == 0
    assert seen == {
        "out": "/analysis/active_python_import_graph.json",
        "kwargs": {
            "projects": ["sinity-lynchpin"],
            "snapshot_file": "/analysis/active_project_snapshot.json",
        },
    }
