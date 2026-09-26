from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lynchpin.sources.chisel_history import _cache_patch_once, _coherence_reasons, build_history


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def _commit(repo: Path, message: str, body: str | None = None) -> str:
    _git(repo, "add", "-A")
    args = ["commit", "-m", message]
    if body:
        args.extend(["-m", body])
    _git(repo, *args)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    return repo


def test_history_tracks_multiref_merge_rename_binary_and_context(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('one')\n", encoding="utf-8")
    (repo / "src" / "delete.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    base = _commit(repo, "base")

    _git(repo, "switch", "-c", "side")
    (repo / "scratchpad.md").write_text("planning notes\n", encoding="utf-8")
    scratch_sha = _commit(repo, "update scratchpad #17")

    _git(repo, "switch", "main")
    (repo / "src" / "main.py").write_text("print('two')\n", encoding="utf-8")
    _commit(repo, "change implementation")
    _git(repo, "merge", "--no-ff", "side", "-m", "merge side")
    merged = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "main.py").rename(repo / "src" / "renamed.py")
    (repo / "src" / "delete.py").unlink()
    (repo / "binary.dat").write_bytes(b"\x00\x01\xff")
    head = _commit(repo, "rename and binary")

    output = tmp_path / "package"
    cache = tmp_path / "cache"
    result = build_history(repo, output, project="fixture", revision=head, cache_dir=cache)
    assert result["status"] == "complete"
    rows = [json.loads(line) for line in (output / "history" / "commits.jsonl").read_text().splitlines()]
    by_sha = {row["sha"]: row for row in rows}
    assert {base, scratch_sha, merged, head} <= set(by_sha)
    assert len(rows) == 5
    assert by_sha[scratch_sha]["maintained_code_text_additions"] == 0
    assert by_sha[scratch_sha]["all_text_additions"] == 1
    assert by_sha[merged]["all_text_additions"] == 1
    assert "#17" in by_sha[scratch_sha]["references"]
    changes = [json.loads(line) for line in (output / "history" / "changes.jsonl").read_text().splitlines()]
    rename = [row for row in changes if row["sha"] == head and row["old_path"]]
    assert rename and rename[0]["old_path"] == "src/main.py"
    assert rename[0]["path"] == "src/renamed.py"
    assert rename[0]["role"] == rename[0]["old_role"] == "implementation"
    binary = [row for row in changes if row["sha"] == head and row["path"] == "binary.dat"]
    assert binary and binary[0]["binary"]
    assert binary[0]["additions"] is None and binary[0]["deletions"] is None
    deleted = [row for row in changes if row["sha"] == head and row["path"] == "src/delete.py"]
    assert deleted and deleted[0]["change_type"] == "D"
    merged_change = [row for row in changes if row["sha"] == merged]
    assert len(merged_change) == 1 and merged_change[0]["change_type"] == "A"
    patch = (output / "history" / "patches" / f"{merged}.patch").read_text()
    assert "first-parent" in patch
    refs = [json.loads(line) for line in (output / "history" / "refs.jsonl").read_text().splitlines()]
    assert {row["name"] for row in refs} >= {"refs/heads/main", "refs/heads/side"}
    assert (output / "fixture-growth.json").is_file()
    assert result["growth_products"]["summary"]["maintained_code_additions"] > 0


def test_history_keeps_staged_and_unstaged_diffs_separate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a\n", encoding="utf-8")
    head = _commit(repo, "base")
    (repo / "src" / "a.py").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "src/a.py")
    (repo / "src" / "a.py").write_text("unstaged\n", encoding="utf-8")
    result = build_history(repo, tmp_path / "package", project="fixture", revision=head)
    history = tmp_path / "package" / "history"
    assert result["dirty_worktree"]["staged_patch_present"]
    assert result["dirty_worktree"]["unstaged_patch_present"]
    assert "staged" in (history / "staged.patch").read_text()
    assert "unstaged" in (history / "unstaged.patch").read_text()


def test_history_cache_reuses_commit_rows_and_patches(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "x.py").write_text("x\n", encoding="utf-8")
    head = _commit(repo, "one")
    cache = tmp_path / "cache"
    build_history(repo, tmp_path / "one", project="fixture", revision=head, cache_dir=cache)
    second = build_history(repo, tmp_path / "two", project="fixture", revision=head, cache_dir=cache)
    assert second["immutable_commit_cache_rows_reused"] == 1
    assert second["patches"]["cache_reused"] == 1
    cached_patch = next((cache / "patches").rglob(f"{head}.patch"))
    packaged_patch = tmp_path / "two" / "history" / "patches" / f"{head}.patch"
    assert cached_patch.stat().st_ino == packaged_patch.stat().st_ino
    (repo / "y.py").write_text("y\n", encoding="utf-8")
    next_head = _commit(repo, "two", "Related to lynchpin-c00")
    third = build_history(repo, tmp_path / "three", project="fixture", revision=next_head, cache_dir=cache)
    assert third["immutable_commit_cache_rows_reused"] == 1
    commit_rows = [json.loads(line) for line in (tmp_path / "three" / "history" / "commits.jsonl").read_text().splitlines()]
    assert "lynchpin-c00" in next(row for row in commit_rows if row["sha"] == next_head)["references"]


def test_patch_cache_publication_is_atomic_and_never_replaces_linked_entries(tmp_path: Path) -> None:
    cache_entry = tmp_path / "cache" / "commit.patch"
    original = b"original complete patch\n"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _cache_patch_once(cache_entry, original), range(16)))

    package_entry = tmp_path / "package" / "commit.patch"
    package_entry.parent.mkdir()
    package_entry.hardlink_to(cache_entry)
    _cache_patch_once(cache_entry, b"competing writer must not replace\n")

    assert cache_entry.read_bytes() == original
    assert package_entry.read_bytes() == original
    assert cache_entry.stat().st_ino == package_entry.stat().st_ino


def test_history_coherence_error_describes_safe_state_differences() -> None:
    reasons = _coherence_reasons(
        "rev-a", "rev-a", "rev-a", "rev-b",
        [{"name": "refs/heads/main", "object": "a"}],
        [{"name": "refs/heads/main", "object": "b"}, {"name": "refs/heads/new", "object": "c"}],
        (False, False, 0), (True, False, 1),
        {"staged": "a" * 64, "unstaged": "b" * 64},
        {"staged": "c" * 64, "unstaged": "b" * 64},
    )
    rendered = "; ".join(reasons)
    assert "HEAD moved" in rendered
    assert "refs changed (added=1, removed=0, moved=1)" in rendered
    assert "tracked status changed (before staged/unstaged/paths=False/False/0, after=True/False/1)" in rendered
    assert "staged/unstaged patch fingerprint changed (staged)" in rendered
    assert "refs/heads" not in rendered
    assert "a" * 64 not in rendered


def test_history_rename_out_of_maintained_scope_counts_deletion_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    _commit(repo, "add source")
    (repo / "src" / "a.py").rename(repo / "scratchpad.md")
    head = _commit(repo, "move source into notes")
    output = tmp_path / "package"
    build_history(repo, output, project="fixture", revision=head)
    row = next(json.loads(line) for line in (output / "history" / "commits.jsonl").read_text().splitlines()
               if json.loads(line)["sha"] == head)
    assert row["maintained_code_text_additions"] == 0
    # A pure rename has no changed text lines even when the role changes.
    assert row["maintained_code_text_deletions"] == 0
    change = next(json.loads(line) for line in (output / "history" / "changes.jsonl").read_text().splitlines()
                  if json.loads(line)["sha"] == head)
    assert change["change_type"].startswith("R")
    assert change["old_role"] == "implementation"
    assert change["role"] == "context"


def test_history_preserves_tab_and_newline_in_git_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    odd_name = "src/name\twith\nline.py"
    (repo / "src").mkdir()
    (repo / odd_name).write_text("x = 1\n", encoding="utf-8")
    head = _commit(repo, "unusual path")
    output = tmp_path / "package"
    build_history(repo, output, project="fixture", revision=head)
    paths = {json.loads(line)["path"] for line in (output / "history" / "changes.jsonl").read_text().splitlines()}
    assert odd_name in paths


def test_history_patch_extraction_disables_gitattributes_textconv(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    converter = repo / "constant-converter.sh"
    converter.write_text("#!/bin/sh\nprintf 'same converted text\\n'\n", encoding="utf-8")
    converter.chmod(0o755)
    (repo / ".gitattributes").write_text("*.txt diff=raw\n", encoding="utf-8")
    (repo / "sample.txt").write_text("raw-one\n", encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "config", "diff.raw.textconv", str(converter))
    (repo / "sample.txt").write_text("raw-two\n", encoding="utf-8")
    head = _commit(repo, "change raw text")
    (repo / "sample.txt").write_text("raw-three\n", encoding="utf-8")

    output = tmp_path / "package"
    build_history(repo, output, project="fixture", revision=head)
    committed_patch = (output / "history" / "patches" / f"{head}.patch").read_text()
    unstaged_patch = (output / "history" / "unstaged.patch").read_text()
    assert "-raw-one" in committed_patch and "+raw-two" in committed_patch
    assert "-raw-two" in unstaged_patch and "+raw-three" in unstaged_patch
    assert "same converted text" not in committed_patch + unstaged_patch
