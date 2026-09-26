from __future__ import annotations

import json
import shutil
import subprocess
import tarfile

import pytest

from lynchpin.sources import chisel
from lynchpin.sources.chisel_package import verify_history_bundle
from types import SimpleNamespace
from datetime import datetime, timezone


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not shutil.which("repomix") or not shutil.which("tokei"),
                    reason="real package tools required")
def test_complete_attachment_works_offline_and_failure_retains_it(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("def greet():\n    return 'hello'\n")
    (repo / "src/other.py").write_text("other = True\n")
    (repo / "src/not_in_xml.py").write_text("raw_only = True\n")
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
    monkeypatch.setattr(chisel_context, "read_native_evidence", lambda project: {
        "coverage": "unavailable", "rows": [], "gaps": ["synthetic offline owner"],
    })
    monkeypatch.setattr(chisel_context, "_agentctl_jobs", lambda project: {
        "coverage": "unavailable", "rows": [], "gaps": ["synthetic offline owner"],
    })
    root = tmp_path / "out"
    result = chisel.build_chisel_bundles(output_root=root, max_workers=1)
    assert result["published"], result
    manifest = json.loads((root / "demo/demo-manifest.json").read_text())
    assert "source/Cargo.lock" in {r["name"] for r in manifest["artifacts"]}
    capture = json.loads((root / "demo/capture.json").read_text())
    compressed = json.loads((root / "demo/representations/compressed.json").read_text())
    assert compressed["members"] == [".agent/docs/guide.md", "src/main.py", "src/other.py", "tests/test_main.py"]
    assert compressed["represented"] == compressed["members"]
    assert json.loads((root / "portfolio.json").read_text())["projects"][0]["snapshot_id"] == capture["snapshot_id"]
    extracted = tmp_path / "extracted"
    with tarfile.open(root / "portfolio-all.tar.gz") as archive:
        archive.extractall(extracted, filter="data")
    helper = extracted / "demo/browse.py"
    command = ["python", "-I", str(helper), "--package", str(helper.parent)]
    source = subprocess.run(command + ["source", "src/main.py", "--start", "1", "--end", "2"],
                            check=True, text=True, capture_output=True)
    assert "def greet" in source.stdout
    history = subprocess.run(command + ["history", "--path", "src/main.py"],
                             check=True, text=True, capture_output=True)
    assert "src/main.py" in history.stdout
    old = (root / "portfolio-all.tar.gz").read_bytes()
    monkeypatch.setattr(chisel, "_build_one", lambda *args: {"status": "failed", "error": "injected"})
    failed = chisel.build_chisel_bundles(output_root=root, max_workers=1)
    assert not failed["published"]
    assert (root / "portfolio-all.tar.gz").read_bytes() == old


def test_bundle_and_history_ref_mismatch_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "history").mkdir()
    (tmp_path / "history/refs.jsonl").write_text(
        '{"name":"refs/heads/main","object":"abc"}\n')
    monkeypatch.setattr(chisel, "_run", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, "def refs/heads/main\n", ""))
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
