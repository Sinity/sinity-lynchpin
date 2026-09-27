from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lynchpin.sources import chisel_inventory
from lynchpin.sources.chisel_inventory import capture_inventory, classify_role, verify_capture


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, text=True, capture_output=True).stdout.strip()


def test_classification_keeps_scratch_code_out_of_metrics() -> None:
    assert classify_role(".agent/scratch/notes.py")[0] == "context"
    assert classify_role("scratch.py")[0] == "context"
    assert classify_role("scratchpad.toml")[0] == "context"
    assert classify_role(".agent/scripts/check.sh")[0] == "tooling"
    assert classify_role("README.md")[0] == "documentation"
    assert classify_role("odd.data")[0] == "unclassified"
    assert classify_role("Cargo.lock")[0] == "evidence"
    assert classify_role("package-lock.json")[0] == "evidence"
    assert classify_role("pnpm-lock.yaml")[0] == "evidence"
    assert classify_role("go.sum")[0] == "evidence"
    assert classify_role("lynchpin/sources/code_snapshots.py")[0] == "implementation"
    assert classify_role("tests/test_snapshots.py")[0] == "tests"
    assert classify_role("docs/fixtures/example.py")[0] == "documentation"
    assert classify_role("tests/fixtures/example.py")[0] == "evidence"
    assert classify_role("hosts/default.nix")[0] == "tooling"
    assert classify_role("modules/example.nix")[0] == "tooling"
    assert classify_role("scripts/build.sh")[0] == "tooling"
    assert classify_role(".tokeignore")[0] == "tooling"
    assert classify_role("devtools/verify.py", project="polylogue")[0] == "tooling"
    assert classify_role("devtools/run_tests.py", project="polylogue")[0] == "tooling"
    assert classify_role("devtools/test_route.py", project="polylogue")[0] == "tests"


def test_inventory_captures_membership_and_unassigned_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / ".agent" / "scratch").mkdir(parents=True)
    (repo / ".agent" / "scripts").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('source')\n")
    (repo / "docs" / "guide.md").write_text("guide\n")
    (repo / "Cargo.lock").write_text("version = 3\n")
    (repo / ".agent" / "scratch" / "memo.py").write_text("this is prose\n")
    (repo / ".agent" / "scripts" / "check.sh").write_text("echo check\n")
    _git(repo, "add", "src/main.py", "docs/guide.md", "Cargo.lock")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")

    plan = SimpleNamespace(
        name="demo",
        path=repo,
        extra_ignore=(),
        slices=(SimpleNamespace(name="source", include=("src/**/*.py",), extra_ignore=()),),
    )
    result = capture_inventory(
        plan,
        tmp_path / "package",
        default_ignore=("*.lock", ".agent/scratch/**", ".agent/scripts/**"),
        scratchpad_include=(".agent/scratch/**/*.py",),
        accelerant_include=(".agent/scripts/**/*.sh",),
    )
    by_path = {row.path: row for row in result.files}
    assert result.memberships["source"] == ("src/main.py",)
    assert by_path[".agent/scratch/memo.py"].role == "context"
    assert by_path[".agent/scratch/memo.py"].included_by == ("scratchpad",)
    assert by_path[".agent/scripts/check.sh"].role == "tooling"
    assert by_path["Cargo.lock"].included
    assert by_path["Cargo.lock"].role == "evidence"
    assert (result.root / "docs/guide.md").read_text() == "guide\n"
    assert hashlib.sha256((result.root / "src/main.py").read_bytes()).hexdigest() == by_path["src/main.py"].sha256
    assert json.loads((tmp_path / "package/capture.json").read_text())["snapshot_id"] == result.snapshot_id


def test_slice_membership_respects_each_slice_ignore(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "x.py").write_text("x\n")
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=(
        SimpleNamespace(name="a", include=("*.py",), extra_ignore=("x.py",)),
        SimpleNamespace(name="b", include=("*.py",), extra_ignore=()),
    ))
    result = capture_inventory(plan, tmp_path / "out")
    assert result.memberships == {"a": (), "b": ("x.py",)}
    assert (result.root / "x.py").exists()


def test_inventory_removes_unsafe_symlinks_from_view_membership(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "links").mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n")
    (repo / "links" / "escape.py").symlink_to(outside)
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=(
        SimpleNamespace(name="links", include=("links/**/*.py",), extra_ignore=()),
    ))
    result = capture_inventory(plan, tmp_path / "out")
    row = next(row for row in result.files if row.path == "links/escape.py")
    assert row.source_kind == "escaping_symlink"
    assert not row.included
    assert result.memberships["links"] == ()


def test_inventory_fails_when_git_file_enumeration_is_unavailable(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    plan = SimpleNamespace(name="demo", path=not_a_repo, extra_ignore=(), slices=())
    with pytest.raises(RuntimeError, match="cannot enumerate Chisel inventory"):
        capture_inventory(plan, tmp_path / "out")


def test_verify_capture_detects_source_content_and_git_status_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "main.py").write_text("before\n")
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=())
    result = capture_inventory(plan, tmp_path / "out")
    verify_capture(repo, result)
    (repo / "main.py").write_text("after!\n")
    with pytest.raises(RuntimeError, match="Git state changed|content changed"):
        verify_capture(repo, result)


def test_snapshot_id_includes_role_policy_and_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    source = repo / "main.py"
    source.write_text("same bytes\n")
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=())
    first = capture_inventory(plan, tmp_path / "one")
    original_classifier = chisel_inventory.classify_role
    monkeypatch.setattr(chisel_inventory, "classify_role", lambda *_args, **_kwargs: ("tooling", "changed policy role"))
    role_changed = capture_inventory(plan, tmp_path / "role")
    assert first.snapshot_id != role_changed.snapshot_id
    monkeypatch.setattr(chisel_inventory, "classify_role", original_classifier)
    monkeypatch.setattr(chisel_inventory, "POLICY_VERSION", "future-policy")
    second = capture_inventory(plan, tmp_path / "two")
    assert first.snapshot_id != second.snapshot_id
    monkeypatch.undo()
    source.chmod(0o755)
    third = capture_inventory(plan, tmp_path / "three")
    assert first.snapshot_id != third.snapshot_id


def test_excluded_file_is_not_read_or_hashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    private = repo / "large-private.dat"
    private.write_bytes(b"private contents")
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=())
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == private:
            raise AssertionError("excluded file content was read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    result = capture_inventory(plan, tmp_path / "out", default_ignore=("large-private.dat",))
    row = next(row for row in result.files if row.path == "large-private.dat")
    assert not row.included
    assert row.sha256 is None
    assert row.size_bytes == len(b"private contents")


def test_ignored_agent_discovery_prunes_cache_and_target_trees(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".agent/target/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "ignore generated agent target")
    (repo / ".agent" / "scratch").mkdir(parents=True)
    (repo / ".agent" / "target").mkdir()
    (repo / ".agent" / "scratch" / "memo.md").write_text("note\n")
    (repo / ".agent" / "target" / "cache.py").write_text("generated\n")
    plan = SimpleNamespace(name="demo", path=repo, extra_ignore=(), slices=())
    result = capture_inventory(plan, tmp_path / "out", scratchpad_include=(".agent/**/*.md", ".agent/**/*.py"))
    paths = {row.path for row in result.files}
    assert ".agent/scratch/memo.md" in paths
    assert ".agent/target/cache.py" not in paths


def test_ignored_agent_candidates_only_enter_explicit_agent_slice(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".agent/docs/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "ignore agent docs")
    (repo / ".agent" / "docs").mkdir(parents=True)
    (repo / ".agent" / "docs" / "guide.py").write_text("documentary example\n")
    plan = SimpleNamespace(
        name="demo",
        path=repo,
        extra_ignore=(),
        slices=(
            SimpleNamespace(name="generic", include=("**/*.py",), extra_ignore=()),
            SimpleNamespace(name="agent-docs", include=(".agent/docs/**/*.py",), extra_ignore=()),
        ),
    )
    result = capture_inventory(plan, tmp_path / "out")
    path = ".agent/docs/guide.py"
    assert result.memberships["generic"] == ()
    assert result.memberships["agent-docs"] == (path,)
    compressed_union = set().union(*(set(paths) for paths in result.memberships.values()))
    assert compressed_union == {path}
