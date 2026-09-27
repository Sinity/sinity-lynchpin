from __future__ import annotations

import json
import shutil
import subprocess
import tarfile

import pytest

from lynchpin.sources.chisel_options import BuildOptions
from lynchpin.sources import chisel
from lynchpin.analysis.projects.chisel import build_chisel_bundles
from lynchpin.sources.chisel_package import evidence_outputs, verify_history_bundle
from types import SimpleNamespace
from datetime import datetime, timezone


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not shutil.which("repomix") or not shutil.which("tokei"),
                    reason="real package tools required")
def test_complete_attachment_works_offline_and_failure_retains_it(
        tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("def greet():\n    return 'hello'\n")
    (repo / "src/other.py").write_text("other = True\n")
    (repo / "src/not_in_xml.py").write_text("raw_only = True\n")
    (repo / "src/snapshot.snap").write_text("neutral snapshot fixture\n")
    (repo / "src/capture.raw").write_text("neutral captured text\n")
    (repo / "src/fixture.key").write_text("neutral key fixture; no secret\n")
    (repo / "src/schema.xml").write_text("<schema>neutral</schema>\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_main.py").write_text("def test_greet():\n    assert True\n")
    (repo / ".agent/scratch").mkdir(parents=True)
    (repo / ".agent/scratch/plan.md").write_text("Context prose and an example:\n```python\nx = 42\n```\n")
    (repo / ".agent/docs").mkdir()
    (repo / ".agent/docs/guide.md").write_text("Explicit ignored project context\n")
    (repo / ".gitignore").write_text(".agent/\n")
    (repo / "Cargo.lock").write_text("version = 3\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "Initial implementation (#12)")
    plan = chisel.RepoPlan("demo", repo, (
        chisel.Slice("core", "source", ("src/**",), ("src/other.py", "src/not_in_xml.py")),
        chisel.Slice("tests", "tests", ("tests/**", "src/other.py")),
        chisel.Slice("context", "context", (".agent/docs/**",)),
    ))
    monkeypatch.setattr(chisel, "REPO_PLANS", {"demo": plan})
    monkeypatch.setattr(chisel, "_ensure_chisel_prerequisites", lambda plans: None)
    monkeypatch.setattr(chisel, "_generate_beads", lambda *args: ([], 0, {"available": False}))
    from lynchpin.sources import chisel_context
    from lynchpin.sources import chisel_excerpts
    monkeypatch.setattr(chisel_excerpts, "collect_excerpts", lambda *_args: {"coverage": "unavailable"})
    monkeypatch.setattr(chisel_context, "read_native_evidence", lambda project: {
        "coverage": "unavailable", "rows": [], "gaps": ["synthetic offline owner"],
    })
    monkeypatch.setattr(chisel_context, "_agentctl_jobs", lambda project: {
        "coverage": "unavailable", "rows": [], "gaps": ["synthetic offline owner"],
    })
    root = tmp_path / "out"
    result = build_chisel_bundles(project_names=["demo"], output_root=root, max_workers=1, options=BuildOptions(target="worktree", xml=True, sqlite=True))
    assert result["published"], result
    console = capsys.readouterr().out
    summary = console.index("Completed 1/1: demo complete")
    stage = console.index("capture source inventory")
    assert summary < stage
    manifest = json.loads((root / "demo/demo-manifest.json").read_text())
    assert "source/Cargo.lock" in {r["name"] for r in manifest["artifacts"]}
    capture = json.loads((root / "demo/capture.json").read_text())
    compressed = json.loads((root / "demo/representations/compressed.json").read_text())
    assert compressed["members"] == [
        ".agent/docs/guide.md", "src/capture.raw", "src/fixture.key",
        "src/main.py", "src/other.py", "src/schema.xml", "src/snapshot.snap", "tests/test_main.py",
    ]
    assert compressed["repomix_filtered_text_raw_only"] == [
        "src/capture.raw", "src/fixture.key", "src/snapshot.snap",
    ]
    assert set(compressed["represented"]) == set(compressed["members"]) - set(
        compressed["repomix_filtered_text_raw_only"])
    # Repomix 1.18.0 filters these textual fixture extensions from XML. Chisel
    # records the limitation while preserving the original bytes in source/.
    core_representation = json.loads((root / "demo/representations/core.json").read_text())
    assert core_representation["repomix_filtered_text_raw_only"] == [
        "src/capture.raw", "src/fixture.key", "src/snapshot.snap",
    ]
    for path in core_representation["repomix_filtered_text_raw_only"]:
        assert (root / "demo/source" / path).read_bytes() == (repo / path).read_bytes()
    assert json.loads((root / "portfolio.json").read_text())["projects"][0]["snapshot_id"] == capture["snapshot_id"]
    extracted = tmp_path / "extracted"
    with tarfile.open(root / "portfolio-all.tar.gz") as archive:
        archive.extractall(extracted, filter="data")
    assert (root / "demo/index.sqlite3").is_file()
    assert not (extracted / "demo/index.sqlite3").exists()
    assert not (extracted / "demo/demo-core.xml").exists()
    assert (extracted / "demo/source/src/schema.xml").is_file()
    assert (extracted / "demo/demo-all-refs.bundle").is_file()
    assert (extracted / "ATTACHMENT_START_HERE.md").is_file()
    helper = extracted / "demo/browse.py"
    command = ["python", "-I", str(helper), "--package", str(helper.parent)]
    source = subprocess.run(command + ["source", "src/main.py", "--start", "1", "--end", "2"],
                            check=True, text=True, capture_output=True)
    assert "def greet" in source.stdout
    history = subprocess.run(command + ["history", "--path", "src/main.py"],
                             check=True, text=True, capture_output=True)
    assert "src/main.py" in history.stdout
    search = subprocess.run(command + ["search", "greet"],
                            check=True, text=True, capture_output=True)
    assert "src/main.py" in search.stdout
    sql = subprocess.run(command + ["sql", "SELECT 1"], text=True, capture_output=True)
    assert sql.returncode == 2 and "full local package" in sql.stderr
    old = (root / "portfolio-all.tar.gz").read_bytes()
    monkeypatch.setattr(chisel, "_build_one", lambda *args: {"status": "failed", "error": "injected"})
    failed = build_chisel_bundles(project_names=["demo"], output_root=root, max_workers=1, options=BuildOptions(target="worktree", xml=True, sqlite=True))
    assert not failed["published"]
    assert (root / "portfolio-all.tar.gz").read_bytes() == old


def test_github_coverage_attachment_preserves_possible_truncation(tmp_path, monkeypatch):
    from lynchpin.sources import chisel_options

    plan = SimpleNamespace(name="demo", github_slug="Sinity/demo", path=tmp_path)
    inventory = SimpleNamespace()
    out_dir = tmp_path / "package"
    out_dir.mkdir()
    monkeypatch.setattr(chisel_options, "active_options", BuildOptions(datasets=()))
    monkeypatch.setattr(chisel, "_github_context_index", {})
    monkeypatch.setattr(chisel, "_github_context_manifest", {
        "refresh_status": "refreshed",
        "inventory_coverage": {
            "demo": {"issue": {"coverage": "possibly_truncated", "total_count": None}}
        },
    })

    evidence_outputs(plan, inventory, out_dir, tmp_path / "cache")

    attachment = json.loads((out_dir / "trackers/github-coverage.json").read_text())
    assert attachment["inventory_coverage"]["issue"]["total_count"] is None
    assert "may be truncated" in attachment["interpretation"]


def test_bundle_and_history_ref_mismatch_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "history").mkdir()
    (tmp_path / "history/refs.jsonl").write_text(
        '{"name":"refs/heads/main","object":"abc"}\n')
    monkeypatch.setattr(chisel, "_run", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, "def refs/heads/main\n", ""))
    with pytest.raises(RuntimeError, match="bundle refs do not match"):
        verify_history_bundle(SimpleNamespace(name="demo", path=tmp_path),
                              SimpleNamespace(revision="abc"), tmp_path)


def test_bundle_accepts_linked_worktree_head_pseudoref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "source.txt").write_text("neutral fixture\n")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-qm", "fixture")
    revision = git(repo, "rev-parse", "HEAD")
    worktree = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "-qb", "linked", str(worktree))
    bundle = tmp_path / "demo-all-refs.bundle"
    git(repo, "bundle", "create", str(bundle), "--all")
    heads = git(repo, "bundle", "list-heads", str(bundle)).splitlines()
    assert any(name == "worktrees/linked-worktree/HEAD"
               for _, name in (line.split(" ", 1) for line in heads))
    refs = [line.split(" ", 1) for line in git(repo, "for-each-ref",
                                                "--format=%(objectname) %(refname)").splitlines()]
    out = tmp_path / "out"
    (out / "history").mkdir(parents=True)
    (out / "history/refs.jsonl").write_text("".join(
        json.dumps({"object": object_id, "name": name}) + "\n"
        for object_id, name in refs))
    (out / "demo-all-refs.bundle").write_bytes(bundle.read_bytes())
    (out / "capture.json").write_text("{}\n")
    verify_history_bundle(SimpleNamespace(name="demo", path=repo),
                          SimpleNamespace(revision=revision), out)


@pytest.mark.parametrize("bundle_heads", [
    "abc refs/heads/main\n",  # missing HEAD
    "abc refs/heads/main\nabc HEAD\ndef refs/tags/unexpected\n",
    "abc refs/heads/main\ndef HEAD\n",
    "def refs/heads/main\nabc HEAD\n",  # true ref points at a different commit
    "abc HEAD\n",  # missing captured true ref
])
def test_bundle_rejects_missing_or_mismatched_head_and_true_refs(
        tmp_path, monkeypatch, bundle_heads):
    (tmp_path / "history").mkdir()
    (tmp_path / "history/refs.jsonl").write_text(
        '{"name":"refs/heads/main","object":"abc"}\n')
    (tmp_path / "capture.json").write_text("{}\n")
    monkeypatch.setattr(chisel, "_run", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, bundle_heads, ""))
    with pytest.raises(RuntimeError, match="bundle refs do not match"):
        verify_history_bundle(SimpleNamespace(name="demo", path=tmp_path),
                              SimpleNamespace(revision="abc"), tmp_path)


def test_substrate_inventory_keeps_nested_source_identity(tmp_path):
    from lynchpin.ingest.code_snapshots_materialize import _results_to_rows

    source = tmp_path / "demo/source"
    source.mkdir(parents=True)
    (source / "schema.xml").write_text("<schema/>\n")
    _, rows = _results_to_rows({"projects": {"demo": {"status": "generated", "published": True}}},
                              datetime.now(timezone.utc), tmp_path)
    assert rows[0]["filename"] == "source/schema.xml"
    assert rows[0]["kind"] == "captured_source"
    _, unpublished = _results_to_rows({"projects": {"demo": {"status": "generated", "published": False}}},
                                     datetime.now(timezone.utc), tmp_path)
    assert unpublished == []
