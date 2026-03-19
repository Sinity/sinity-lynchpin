from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import lynchpin.analysis.projects.rich_bundles as rich_bundles
from lynchpin.analysis.projects.bundles import ProjectSpec


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    _commit(path, "README.md", "# Demo\n", "Initial commit")
    _commit(path, "src/app.py", "print('demo')\n", "Add source")
    _commit(path, "docs/guide.md", "# Guide\n", "Add docs")
    _commit(path, "tests/test_demo.py", "def test_demo():\n    assert True\n", "Add tests")
    _commit(path, "nix/default.nix", "{ }\n", "Add config")


def _write_fake_repomix(path: Path) -> None:
    script = """#!/usr/bin/env python3
import pathlib
import sys

if "--version" in sys.argv:
    print("repomix-test 0.0.1")
    raise SystemExit(0)

args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output") + 1])
header = args[args.index("--header-text") + 1]
include = args[args.index("--include") + 1] if "--include" in args else ""
output.write_text(
    "\\n".join(
        [
            header,
            f"include={include}",
            f"has_line_numbers={'--output-show-line-numbers' in args}",
            f"no_git_sort={'--no-git-sort-by-changes' in args}",
        ]
    )
    + "\\n",
    encoding="utf-8",
)
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_build_rich_project_bundles_generates_slices_and_history(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_repomix = fake_bin_dir / "repomix"
    _write_fake_repomix(fake_repomix)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{os.environ['PATH']}")

    demo_spec = ProjectSpec("demo", repo)
    monkeypatch.setattr(
        rich_bundles,
        "RICH_PROJECT_PLANS",
        {
            "demo": rich_bundles.RichProjectPlan(
                spec=demo_spec,
                slices=(
                    rich_bundles.SliceSpec("runtime", "Runtime slice", ("src/**", "README.md")),
                    rich_bundles.SliceSpec("docs", "Docs slice", ("docs/**", "tests/**")),
                ),
            )
        },
    )
    monkeypatch.setattr(rich_bundles, "PROJECT_SPECS", {"demo": demo_spec})
    monkeypatch.setattr(rich_bundles, "DEFAULT_RICH_PROJECTS", ("demo",))

    output_root = tmp_path / "bundles-rich"
    index = rich_bundles.build_rich_project_bundles(
        project_names=["demo"],
        output_root=output_root,
        patch_window=2,
        summary_window=3,
    )

    assert index["projects"][0]["project"] == "demo"
    bundle_dir = output_root / "demo"
    manifest_path = bundle_dir / "manifest.json"
    overview_path = bundle_dir / "overview.md"
    slice_runtime = bundle_dir / "slice-runtime.md"
    slice_docs = bundle_dir / "slice-docs.md"
    patches_1 = bundle_dir / "history-patches-2" / "0001.md"
    patches_3 = bundle_dir / "history-patches-2" / "0003.md"
    summary_1 = bundle_dir / "history-summary-3" / "0001.md"
    summary_2 = bundle_dir / "history-summary-3" / "0002.md"

    assert manifest_path.exists()
    assert overview_path.exists()
    assert slice_runtime.exists()
    assert slice_docs.exists()
    assert patches_1.exists()
    assert patches_3.exists()
    assert summary_1.exists()
    assert summary_2.exists()
    assert (bundle_dir / "README.md").exists()
    assert (output_root / "README.md").exists()
    assert (output_root / "index.json").exists()

    assert "include=src/**,README.md" in slice_runtime.read_text(encoding="utf-8")
    assert "include=docs/**,tests/**" in slice_docs.read_text(encoding="utf-8")
    assert "no_git_sort=True" in slice_runtime.read_text(encoding="utf-8")
    assert "Window commits: `2`" in patches_1.read_text(encoding="utf-8")
    assert "Window commits: `1`" in patches_3.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_generation"] == "project-context-rich-bundle-v1"
    assert manifest["project"] == "demo"
    assert len(manifest["slice_outputs"]) == 2
    assert manifest["history"]["patch_window"] == 2
    assert manifest["history"]["patch_commit_count"] == 5
    assert manifest["history"]["patch_shard_count"] == 3
    assert manifest["history"]["summary_window"] == 3
    assert manifest["history"]["summary_commit_count"] == 5
    assert manifest["history"]["summary_shard_count"] == 2
    assert manifest["inventory"]["tracked_file_count"] == 5
    assert "repomix" in manifest["slice_outputs"][0]["command"]
    assert "src/**,README.md" in manifest["slice_outputs"][0]["command"]

    root_index = json.loads((output_root / "index.json").read_text(encoding="utf-8"))
    assert root_index["schema_generation"] == "project-context-rich-bundle-index-v1"
    assert [row["project"] for row in root_index["projects"]] == ["demo"]
