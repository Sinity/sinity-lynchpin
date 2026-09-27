from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from lynchpin.sources import chisel


def test_root_index_persists_worker_measured_stage_timings(tmp_path: Path) -> None:
    plan = chisel.RepoPlan(name="alpha", path=tmp_path / "alpha", slices=())
    project_dir = tmp_path / "out" / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "alpha-manifest.json").write_text(
        json.dumps({"artifacts": [], "git": {"branch": "main"}}),
        encoding="utf-8",
    )
    timings = [
        {
            "stage": "slice",
            "label": "core",
            "started_at": "2026-09-26T16:00:00+00:00",
            "finished_at": "2026-09-26T16:00:03+00:00",
            "queue_wait_s": 1.25,
            "elapsed_s": 3.0,
            "repomix_wait_s": 0.75,
            "repomix_run_s": 2.0,
        }
    ]

    json_path, _ = chisel._write_root_index(
        tmp_path / "out",
        [plan],
        {
            "alpha": {
                "status": "generated",
                "elapsed_s": 4.5,
                "stage_timings": timings,
            }
        },
        "2026-09-26T160000Z",
        "test",
        4.5,
    )

    index = json.loads((tmp_path / "out" / json_path).read_text(encoding="utf-8"))
    assert index["projects"][0]["elapsed_s"] == 4.5
    assert index["projects"][0]["stage_timings"] == timings


def test_index_and_terminal_counts_mark_local_github_observations(tmp_path: Path) -> None:
    plan = chisel.RepoPlan(name="alpha", path=tmp_path / "alpha", slices=())
    project_dir = tmp_path / "out" / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "alpha-overview.json").write_text(
        json.dumps({"counts": {
            "issues_open": 0, "issues_closed": 8, "prs_open": 2, "prs_merged": 10,
            "issues_open_current": None, "prs_open_current": None,
            "issues_open_count_coverage": "unavailable",
            "prs_open_count_coverage": "possibly_truncated",
        }}), encoding="utf-8",
    )

    _, markdown = chisel._write_root_index(
        tmp_path / "out", [plan], {"alpha": {"status": "generated"}},
        "2026-09-27T000000Z", "test", 1.0,
    )

    index_text = (tmp_path / "out" / markdown).read_text(encoding="utf-8")
    assert "unknown (local 0)" in index_text
    assert "unknown (at least 2 observed)" in index_text
    assert chisel._github_summary_count(
        {"issues_open": 0, "issues_closed": 8, "issues_open_current": None}, "issues"
    ) == "? (local 0o/8c)"
    assert chisel._github_summary_count(
        {"prs_open": 2, "prs_merged": 10, "prs_open_current": None,
         "prs_open_count_coverage": "possibly_truncated"}, "prs"
    ) == "? (observed 2o/10m)"
    assert chisel._github_summary_count(
        {"prs_open": 2, "prs_merged": 10, "prs_open_current": 2}, "prs"
    ) == "2o/10m"


@pytest.mark.parametrize("detailed", [False, True])
def test_project_log_block_cannot_be_interleaved_by_other_worker(monkeypatch, detailed) -> None:
    from lynchpin.sources.chisel_options import BuildOptions

    monkeypatch.setattr(chisel.chisel_options, "active_options", BuildOptions(xml=detailed))
    printed: list[str] = []
    worker: threading.Thread | None = None

    def fake_print(message="", **_kwargs):
        nonlocal worker
        printed.append(str(message))
        if "alpha complete" in str(message):
            worker = threading.Thread(
                target=chisel._print_live, args=("unrelated worker event",)
            )
            worker.start()

    monkeypatch.setattr(chisel, "_print", fake_print)
    chisel._print_project_summary(
        1,
        2,
        {
            "project": "alpha",
            "status": "generated",
            "elapsed_s": 1.0,
            "log_lines": ["alpha detail one", "alpha detail two"],
        },
    )
    assert worker is not None
    worker.join(timeout=1)

    assert not worker.is_alive()
    if detailed:
        assert printed[1:3] == ["alpha detail one", "alpha detail two"]
        assert printed[3] == "unrelated worker event"
    else:
        assert printed[1:] == ["unrelated worker event"]


def test_cli_returns_failure_when_any_project_is_partial(monkeypatch) -> None:
    monkeypatch.setattr(
        chisel,
        "build_chisel_bundles",
        lambda **_kwargs: {
            "projects": {
                "alpha": {"status": "generated"},
                "beta": {"status": "partial"},
            }
        },
    )

    from lynchpin.cli.chisel import main

    assert main([]) == 1


def test_cli_returns_success_when_all_projects_generated(monkeypatch) -> None:
    monkeypatch.setattr(
        chisel,
        "build_chisel_bundles",
        lambda **_kwargs: {
            "projects": {
                "alpha": {"status": "generated"},
                "beta": {"status": "generated"},
            }
        },
    )

    from lynchpin.cli.chisel import main

    assert main([]) == 0


def test_projects_typer_command_exits_nonzero_for_partial_project(monkeypatch) -> None:
    from lynchpin.analysis.projects import cli as projects_cli
    from lynchpin.sources import chisel as source_chisel

    monkeypatch.setattr(source_chisel, "build_chisel_bundles",
        lambda **_kwargs: {"projects": {"alpha": {"status": "partial"}}})
    assert projects_cli.main(["chisel", "--max-workers", "1"]) == 1


def test_portable_sidecar_failure_is_reported(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(name="alpha", path=tmp_path / "alpha", slices=())
    plan.path.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def fake_run(cmd, *, cwd=None):
        if cmd[:3] == ["git", "bundle", "create"]:
            return subprocess.CompletedProcess(cmd, 1, "", "bundle failed")
        Path(cmd[2]).write_bytes(b"archive")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(chisel, "_run", fake_run)
    monkeypatch.setattr(chisel, "_repo_tree", lambda *_args, **_kwargs: ".\n")

    with pytest.raises(chisel.MaterializationError, match="git bundle: bundle failed"):
        chisel._generate_portable_sidecars(plan, out_dir)
