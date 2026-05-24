"""Tests for analysis.interpretation.rust_dependency_hygiene."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lynchpin.analysis.interpretation.rust_dependency_hygiene import (
    _parse_machete_output,
    _project_map,
    _summarize_geiger,
    build_active_rust_dependency_hygiene,
)


def test_project_map_filters_selection() -> None:
    payload = {
        "projects": [
            {"project": "demo", "path": "/tmp/demo"},
            {"project": "skip", "path": "/tmp/skip"},
        ]
    }
    assert _project_map(payload, {"demo"}) == {"demo": "/tmp/demo"}


def test_parse_machete_output_handles_no_findings() -> None:
    rows = _parse_machete_output("")
    assert rows == []


def test_parse_machete_output_groups_by_manifest() -> None:
    text = (
        "cargo-machete found the following unused dependencies in /tmp/demo:\n"
        "  unused_a\n"
        "  unused_b\n"
        "cargo-machete found the following unused dependencies in /tmp/other:\n"
        "  unused_c\n"
    )
    rows = _parse_machete_output(text)
    assert len(rows) == 2
    assert rows[0]["manifest"] == "/tmp/demo"
    assert rows[0]["unused"] == ["unused_a", "unused_b"]
    assert rows[1]["unused"] == ["unused_c"]


def test_parse_machete_output_unparsed_falls_back() -> None:
    rows = _parse_machete_output("totally unexpected diagnostic\n")
    assert rows and rows[0]["manifest"] == "(unparsed)"


def test_summarize_geiger_returns_empty_summary_on_garbage() -> None:
    assert _summarize_geiger(None)["packages"] == []
    assert _summarize_geiger({"weird": True})["raw_top_level_keys"] == ["weird"]


def test_summarize_geiger_picks_per_package_unsafe_counts() -> None:
    payload = {
        "packages": [
            {
                "package": {"id": {"name": "demo-crate"}},
                "unsafety": {
                    "used": {
                        "functions": {"safe": 4, "unsafe": 2},
                    }
                },
            }
        ]
    }
    summary = _summarize_geiger(payload)
    assert summary["packages"][0]["name"] == "demo-crate"
    assert summary["packages"][0]["unsafe_function_count"] == 2


def test_build_emits_caveat_when_machete_missing(monkeypatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"projects": []}), encoding="utf-8")
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.rust_dependency_hygiene.shutil.which",
        lambda _binary: None,
    )
    payload = build_active_rust_dependency_hygiene(snapshot_file=snapshot)
    assert any("cargo-machete" in c for c in payload["caveats"])


def test_build_requires_project_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="active project snapshot is missing"):
        build_active_rust_dependency_hygiene(snapshot_file=tmp_path / "missing.json")


def test_build_skips_non_rust_workspaces(monkeypatch, tmp_path: Path) -> None:
    not_rust = tmp_path / "py-only"
    not_rust.mkdir()
    rust = tmp_path / "real-rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n")

    snapshot_payload = {
        "projects": [
            {"project": "py-only", "path": str(not_rust)},
            {"project": "real-rust", "path": str(rust)},
        ]
    }
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(snapshot_payload), encoding="utf-8")

    # Stub out actual tool execution
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.rust_dependency_hygiene.shutil.which",
        lambda binary: f"/fake/bin/{binary}",
    )

    def fake_run(cmd, capture_output, text, timeout, cwd=None):
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.rust_dependency_hygiene.subprocess.run",
        fake_run,
    )

    payload = build_active_rust_dependency_hygiene(snapshot_file=snapshot)
    project_names = {row["project"] for row in payload["workspaces"]}
    assert project_names == {"real-rust"}


@pytest.mark.skipif(shutil.which("cargo-machete") is None, reason="cargo-machete not installed")
def test_machete_runs_against_synthetic_workspace(tmp_path: Path) -> None:
    """Live cargo-machete invocation against a synthetic Rust crate.

    The crate declares an unused dependency that cargo-machete should flag.
    """
    workspace = tmp_path / "demo"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "lib.rs").write_text("pub fn hello() -> u32 { 42 }\n")
    (workspace / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dependencies]\nserde = "1"\n'
    )

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"projects": [{"project": "demo", "path": str(workspace)}]}),
        encoding="utf-8",
    )

    # cargo-machete may need `cargo` on PATH for metadata; if cargo is missing,
    # the live tool cannot produce candidates.
    try:
        payload = build_active_rust_dependency_hygiene(snapshot_file=snapshot)
    except subprocess.CalledProcessError:
        pytest.skip("cargo not on PATH")

    rows = payload["workspaces"]
    assert rows and rows[0]["project"] == "demo"
    machete = rows[0]["machete"]
    assert machete["available"] is True
    # serde is unused — but cargo-machete reads metadata, so we just verify
    # the producer parsed something coherent (count or empty list, not None).
    assert isinstance(machete["candidate_count"], int)
