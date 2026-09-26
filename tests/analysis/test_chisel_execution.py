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


def test_project_log_block_cannot_be_interleaved_by_other_worker(monkeypatch) -> None:
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
    assert printed[1:3] == ["alpha detail one", "alpha detail two"]
    assert printed[3] == "unrelated worker event"


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

    assert chisel.run_from_cli([]) == 1


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

    assert chisel.run_from_cli([]) == 0


def test_projects_typer_command_exits_nonzero_for_partial_project(monkeypatch) -> None:
    import typer

    from lynchpin.analysis.projects import cli as projects_cli
    from lynchpin.analysis.projects import chisel as projects_chisel

    monkeypatch.setattr(
        projects_chisel,
        "build_chisel_bundles",
        lambda **_kwargs: {"projects": {"alpha": {"status": "partial"}}},
    )

    with pytest.raises(typer.Exit) as exc_info:
        projects_cli._chisel(
            projects="", output_root="", max_workers=1, list_only=False
        )

    assert exc_info.value.exit_code == 1


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
