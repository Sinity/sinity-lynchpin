from pathlib import Path
import json
import subprocess

from lynchpin.sources.chisel import RepoPlan
from lynchpin.sources.chisel_options import BuildOptions
from lynchpin.sources.chisel_snapshots import capture_catalogue, verify_snapshot


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def test_default_source_and_dirty_overlay_survive_later_development(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "main.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "candidate")
    (repo / "main.py").write_text("value = 2\n")
    git(repo, "commit", "-am", "Candidate")
    (repo / "main.py").write_text("value = 3\n")
    (repo / "extra.py").write_text("extra = True\n")
    package = tmp_path / "package"
    inventory = capture_catalogue(RepoPlan("sample", repo, ()), package,
        BuildOptions(refs=(("sample", "candidate"),)))
    assert inventory.revision == main
    assert (package / "source/main.py").read_text() == "value = 1\n"
    assert (package / "snapshots/worktree/files/main.py").read_text() == "value = 3\n"
    assert (package / "snapshots/candidate-1/files/main.py").read_text() == "value = 2\n"
    assert json.loads((package / "snapshots/worktree/manifest.json").read_text())["dirty"] is True
    (repo / "main.py").write_text("later = 4\n")
    git(repo, "branch", "-f", "main", "HEAD")
    verify_snapshot(inventory)


def test_pinned_capture_ignores_export_ignore_attributes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / ".gitattributes").write_text("main.py export-ignore\n")
    (repo / "main.py").write_text("print(1)\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    capture_catalogue(RepoPlan("sample", repo, ()), tmp_path / "package", BuildOptions())
    assert (tmp_path / "package/source/main.py").read_text() == "print(1)\n"


def test_frozen_history_survives_live_ref_moves(tmp_path):
    from lynchpin.sources.chisel_history import build_history, freeze_refs
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "main.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    original = git(repo, "rev-parse", "HEAD")
    with freeze_refs(repo, tmp_path) as frozen:
        (repo / "main.py").write_text("value = 2\n")
        git(repo, "commit", "-am", "Later")
        result = build_history(Path(frozen), tmp_path / "package", project="fixture", revision="HEAD", frozen=True)
        assert result["revision"] == original
        assert result["commit_count"] == 1
        git(Path(frozen), "bundle", "create", str(tmp_path / "history.bundle"), "--all")
        assert original in git(repo, "bundle", "list-heads", str(tmp_path / "history.bundle"))
