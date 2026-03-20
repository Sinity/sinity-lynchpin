from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.projects import cli as projects_cli


def test_velocity_cli_dispatches_to_dashboard(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_build_velocity_dashboard(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(projects_cli, "build_velocity_dashboard", fake_build_velocity_dashboard)

    exit_code = projects_cli.main(
        [
            "velocity",
            "--output",
            "artefacts/meta/velocity/test.html",
            "--projects",
            "sinex polylogue",
            "--exclude",
            "sinnix",
            "--aggregate",
            "false",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["output"] == Path("artefacts/meta/velocity/test.html")
    assert captured["project_names"] == ["sinex", "polylogue"]
    assert captured["exclude_names"] == ["sinnix"]
    assert captured["aggregate"] is False
    assert captured["log"] is print
    assert "Velocity dashboard written" in out


def test_bundles_cli_dispatches_to_bundle_builder(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_build_project_bundles(**kwargs):
        captured.update(kwargs)
        return {"output_root": "/tmp/bundles"}

    monkeypatch.setattr(projects_cli, "build_project_bundles", fake_build_project_bundles)

    exit_code = projects_cli.main(
        [
            "bundles",
            "--output-root",
            "/tmp/bundles",
            "--projects",
            "sinex sinnix",
            "--logs-count",
            "50",
            "--include-diffs",
            "true",
            "--include-compressed",
            "false",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["output_root"] == Path("/tmp/bundles")
    assert captured["project_names"] == ["sinex", "sinnix"]
    assert captured["logs_count"] == 50
    assert captured["include_diffs"] is True
    assert captured["include_compressed"] is False
    assert captured["log"] is print
    assert "Project bundle index written to /tmp/bundles/index.json" in out


def test_rich_bundles_cli_dispatches_to_rich_bundle_builder(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_build_rich_project_bundles(**kwargs):
        captured.update(kwargs)
        return {"output_root": "/tmp/rich"}

    monkeypatch.setattr(projects_cli, "build_rich_project_bundles", fake_build_rich_project_bundles)

    exit_code = projects_cli.main(
        [
            "rich-bundles",
            "--output-root",
            "/tmp/rich",
            "--projects",
            "sinex",
            "--patch-window",
            "12",
            "--summary-window",
            "120",
            "--patch-commits",
            "",
            "--summary-commits",
            "300",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["output_root"] == Path("/tmp/rich")
    assert captured["project_names"] == ["sinex"]
    assert captured["patch_window"] == 12
    assert captured["summary_window"] == 120
    assert captured["patch_commits"] is None
    assert captured["summary_commits"] == 300
    assert captured["log"] is print
    assert "Rich project bundle index written to /tmp/rich/index.json" in out
