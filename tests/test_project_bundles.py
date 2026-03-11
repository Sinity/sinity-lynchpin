from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import lynchpin.views.project_bundles as project_bundles


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        check=True,
        capture_output=True,
    )


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
ignore = args[args.index("--ignore") + 1] if "--ignore" in args else ""
mode = "compressed" if "--compress" in args else "full"
output.write_text(
    "\\n".join(
        [
            header,
            f"mode={mode}",
            f"ignore={ignore}",
            f"has_line_numbers={'--output-show-line-numbers' in args}",
            f"has_security_skip={'--no-security-check' in args}",
        ]
    )
    + "\\n",
    encoding="utf-8",
)
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_build_repomix_command_switches_flags() -> None:
    spec = project_bundles.ProjectSpec(
        "demo",
        Path("/tmp/demo"),
        extra_ignore=("custom/**",),
    )
    git = project_bundles.GitState(branch="main", commit="abc123", dirty=False)

    full_cmd = project_bundles.build_repomix_command(
        repomix_bin="repomix",
        output_path=Path("/tmp/context.md"),
        spec=spec,
        git=git,
        generated_at="2026-03-11T00:00:00Z",
        variant="full",
        include_diffs=False,
        logs_count=5,
        compressed=False,
    )
    compressed_cmd = project_bundles.build_repomix_command(
        repomix_bin="repomix",
        output_path=Path("/tmp/context-compressed.md"),
        spec=spec,
        git=git,
        generated_at="2026-03-11T00:00:00Z",
        variant="compressed",
        include_diffs=True,
        logs_count=5,
        compressed=True,
    )

    assert "--no-security-check" in full_cmd
    assert "--output-show-line-numbers" in full_cmd
    assert "--compress" not in full_cmd
    assert "--compress" in compressed_cmd
    assert "--remove-empty-lines" in compressed_cmd
    assert "--output-show-line-numbers" not in compressed_cmd
    assert "--include-diffs" in compressed_cmd


def test_main_generates_project_bundle_outputs(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_repomix = fake_bin_dir / "repomix"
    _write_fake_repomix(fake_repomix)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{os.environ['PATH']}")

    spec = project_bundles.ProjectSpec("demo", repo, extra_ignore=("custom/**",))
    monkeypatch.setattr(project_bundles, "PROJECT_SPECS", {"demo": spec})

    output_root = tmp_path / "bundles"
    stale_dir = output_root / "demo"
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale.txt").write_text("stale\n", encoding="utf-8")

    exit_code = project_bundles.main(
        ["--projects", "demo", "--output-root", str(output_root), "--logs-count", "5"]
    )

    assert exit_code == 0
    bundle_dir = output_root / "demo"
    full_path = bundle_dir / "demo-bundle.md"
    compressed_path = bundle_dir / "demo-bundle-compressed.md"
    manifest_path = bundle_dir / "manifest.json"
    index_path = output_root / "index.json"

    assert not (bundle_dir / "stale.txt").exists()
    assert full_path.exists()
    assert compressed_path.exists()
    assert manifest_path.exists()
    assert (bundle_dir / "README.md").exists()
    assert (output_root / "README.md").exists()
    assert index_path.exists()

    assert "mode=full" in full_path.read_text(encoding="utf-8")
    assert "mode=compressed" in compressed_path.read_text(encoding="utf-8")
    assert "has_line_numbers=True" in full_path.read_text(encoding="utf-8")
    assert "has_line_numbers=False" in compressed_path.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_generation"] == "project-context-bundle-v1"
    assert manifest["project"] == "demo"
    assert manifest["bundle_dir"] == str(bundle_dir)
    assert manifest["repomix_version"] == "repomix-test 0.0.1"
    assert len(manifest["outputs"]) == 2
    assert "--no-security-check" in manifest["outputs"][0]["command"]
    assert "demo-bundle.md" in manifest["outputs"][0]["command"]

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["schema_generation"] == "project-context-bundle-index-v1"
    assert [row["project"] for row in index["projects"]] == ["demo"]
