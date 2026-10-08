"""Retained Chisel views are current only while their selected Git content is."""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from lynchpin.sources.chisel import RepoPlan
from lynchpin.sources.chisel_options import BuildOptions
from lynchpin.sources.chisel_snapshots import capture_catalogue, view_currentness


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _committed_repo(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "main.py").write_text("value = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    return root


def _commit_keeping_head_mtime(repo: Path, text: str) -> None:
    head = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "HEAD"
    before = head.stat().st_mtime_ns
    (repo / "main.py").write_text(text)
    git(repo, "commit", "-am", text.strip())
    os.utime(head, ns=(before, before))
    assert head.stat().st_mtime_ns == before


def test_default_view_follows_selected_commit_not_head_mtime(tmp_path):
    repo = _committed_repo(tmp_path / "repo")
    pinned = git(repo, "rev-parse", "HEAD")
    package = tmp_path / "package"
    capture_catalogue(RepoPlan("sample", repo, ()), package, BuildOptions(refs=(("sample", pinned),)))
    assert view_currentness(repo, package)["state"] == "current"

    # Commit-pinned views do not follow live edits.
    (repo / "main.py").write_text("live edit\n")
    assert view_currentness(repo, package)["state"] == "current"

    _commit_keeping_head_mtime(repo, "value = 2\n")
    result = view_currentness(repo, package)
    assert result["state"] == "stale"
    assert "primary" in result["reason"]
    assert "candidate-1" not in result["reason"]


def test_checkout_view_compares_relevant_dirty_overlay(tmp_path):
    repo = _committed_repo(tmp_path / "repo")
    (repo / "main.py").write_text("dirty = 1\n")
    package = tmp_path / "package"
    capture_catalogue(RepoPlan("sample", repo, ()), package, BuildOptions(target="worktree"))
    assert view_currentness(repo, package)["state"] == "current"

    (repo / "main.py").write_text("dirty = 2\n")
    assert "main.py" in view_currentness(repo, package)["reason"]
    (repo / "main.py").write_text("dirty = 1\n")
    assert view_currentness(repo, package)["state"] == "current"

    (repo / "new.py").write_text("untracked = True\n")
    assert view_currentness(repo, package)["state"] == "stale"
    (repo / "new.py").unlink()
    git(repo, "checkout", "--", "main.py")
    assert view_currentness(repo, package)["state"] == "stale"


def test_linked_worktree_and_missing_inputs_are_explicit(tmp_path):
    repo = _committed_repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "feature", str(linked))
    assert (linked / ".git").is_file()
    (linked / "main.py").write_text("feature = 1\n")
    package = tmp_path / "package"
    capture_catalogue(RepoPlan("sample", linked, ()), package, BuildOptions(target="worktree"))
    assert view_currentness(linked, package)["state"] == "current"

    _commit_keeping_head_mtime(linked, "feature = 2\n")
    assert view_currentness(linked, package)["state"] == "stale"

    assert view_currentness(linked, tmp_path / "never")["state"] == "never_captured"
    assert view_currentness(tmp_path / "gone", package)["state"] == "unavailable"


def test_selected_projects_report_promotion_and_coverage(monkeypatch, tmp_path):
    from lynchpin.ingest.code_snapshots_materialize import code_snapshots_currentness

    root = tmp_path / "packages"
    root.mkdir()
    current = _committed_repo(tmp_path / "current")
    lagging = _committed_repo(tmp_path / "lagging")
    for name, repo in (("current", current), ("lagging", lagging)):
        capture_catalogue(RepoPlan(name, repo, ()), root / name, BuildOptions())
    plans = {name: RepoPlan(name, tmp_path / name, ())
             for name in ("current", "lagging", "unpromoted", "never", "gone")}
    for name in ("unpromoted", "gone"):
        repo = _committed_repo(tmp_path / name)
        capture_catalogue(RepoPlan(name, repo, ()), root / name, BuildOptions())
    _committed_repo(tmp_path / "never")
    shutil.rmtree(tmp_path / "gone")
    promoted = [("current", git(current, "rev-parse", "HEAD")), ("lagging", "0" * 40)]

    class Conn:
        def execute(self, *_args):
            return self

        def fetchall(self):
            return promoted

    @contextmanager
    def connect(**_kwargs):
        yield Conn()

    monkeypatch.setattr("lynchpin.substrate.connection.connect", connect)
    monkeypatch.setattr("lynchpin.sources.chisel_options.DEFAULT_PROJECTS", tuple(plans))
    monkeypatch.setattr("lynchpin.sources.code_snapshots.REPO_PLANS", plans)
    monkeypatch.setattr("lynchpin.sources.code_snapshots.code_snapshots_path", lambda name: root / name)

    report = code_snapshots_currentness()
    states = {row["project"]: row["state"] for row in report["project"]}
    assert states == {"current": "current", "lagging": "stale",
                      "unpromoted": "never_captured", "never": "never_captured",
                      "gone": "unavailable"}
    assert report["state"] == "stale"
    assert "gone unavailable" in report["reason"]
