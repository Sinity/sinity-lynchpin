import hashlib
import json
import os
import subprocess
import tarfile

from lynchpin.analysis.projects.chisel_reports import task_graph
from lynchpin.cli.chisel import main
from lynchpin.sources.chisel_attachments import build_attachments
from lynchpin.sources.chisel_options import BuildOptions
from lynchpin.sources.chisel_structure import _python_imports


def test_cli_default_and_safe_selection(monkeypatch):
    from lynchpin.sources import chisel

    calls = []
    monkeypatch.setattr(chisel, "build_chisel_bundles", lambda **kw: calls.append(kw) or {"published": True})
    assert main([]) == 0
    assert calls[0]["project_names"] == ["sinex", "sinnix", "polylogue", "sinity-lynchpin"]
    assert calls[0]["options"] == BuildOptions()
    assert main(["knowledgebase", "--ref", "knowledgebase=branch with spaces", "--output", "/path with spaces"]) == 0
    assert calls[1]["options"].refs == (("knowledgebase", "branch with spaces"),)


def test_import_scope_guards_and_occurrences():
    import ast

    source = """from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import library as types
try:
    import library
except ImportError:
    pass
def run():
    import library
"""
    rows = _python_imports("sample", "snapshot", "main.py", {"sha256": "a"}, ast.parse(source), {"main", "library"})
    imports = [r for r in rows if r["target_module"] == "library"]
    assert len(imports) == 3
    assert imports[0]["type_only"] is True
    assert imports[0]["alias"] == "types"
    assert imports[1]["optional"] is True
    assert imports[2]["deferred"] is True
    assert imports[2]["enclosing_symbol"] == "run"


def test_partial_task_graph_does_not_claim_complete_count():
    tasks = [{"id": "root", "status": "open", "dependencies": [{"depends_on_id": "missing", "type": "blocks"}]}]
    result = task_graph(tasks, ["root"])
    assert result["campaign_count"] is None
    assert result["missing_nodes"] == ["missing"]
    assert task_graph(tasks, [])["campaign_count"] is None


def test_split_parts_reconstruct_and_verify_raw_xml(tmp_path):
    root = tmp_path / "package"
    source = root / "sample/source"
    source.mkdir(parents=True)
    data = os.urandom(30_000)
    (source / "large.bin").write_bytes(data)
    (source / "schema.xml").write_text("<original/>\n")
    (root / "portfolio.json").write_text("{}")
    manifest = build_attachments(root, ["sample"], limit=4096)
    assert len(manifest["attachments"]) > 1
    extracted = tmp_path / "extracted"
    for row in manifest["attachments"]:
        path = root / row["path"]
        assert path.stat().st_size <= 4096
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        with tarfile.open(path) as archive:
            archive.extractall(extracted, filter="data")
    subprocess.run(["python", "-I", str(extracted / "reconstruct.py")], cwd=extracted, check=True)
    assert (extracted / "sample/source/large.bin").read_bytes() == data
    assert (extracted / "sample/source/schema.xml").read_text() == "<original/>\n"
    assert json.loads((root / "attachments.json").read_text())["projects"] == ["sample"]


def test_default_attachments_include_portfolio_and_individual_projects(tmp_path):
    root = tmp_path / "package"
    for name in ("first", "second"):
        source = root / name / "source"
        source.mkdir(parents=True)
        (source / "main.py").write_text(f"PROJECT = {name!r}\n")
    (root / "portfolio.json").write_text('{"projects": []}')

    manifest = build_attachments(root, ["first", "second"], limit=500_000_000)

    assert {row["path"] for row in manifest["attachments"]} == {
        "portfolio-all.tar.gz", "first-all.tar.gz", "second-all.tar.gz",
    }
    for row in manifest["attachments"]:
        assert row["companions"] == []
        assert (root / row["path"]).stat().st_size <= manifest["limit_bytes"]
    with tarfile.open(root / "first-all.tar.gz") as archive:
        names = set(archive.getnames())
    assert "first/source/main.py" in names
    assert "second/source/main.py" not in names
    assert "portfolio.json" in names


def test_default_context_never_invokes_network(monkeypatch):
    from lynchpin.sources import chisel, chisel_options

    monkeypatch.setattr(chisel_options, "active_options", BuildOptions())
    monkeypatch.setattr(chisel, "_github_context_ready", None)
    monkeypatch.setattr(chisel, "_build_github_context_index", lambda: {})
    def forbidden(**kwargs):
        raise AssertionError("network materialization invoked")
    monkeypatch.setattr("lynchpin.ingest.github_context_materialize.materialize_github_context", forbidden)
    chisel._ensure_github_context_for_chisel({"sample"})
    assert chisel._github_context_manifest["refresh_status"] == "local_only"


def test_content_match_preserves_partial_scope_and_changed_endpoints():
    from lynchpin.analysis.projects.chisel_reports import content_match
    files = [{"path": "main.py", "included": True, "sha256": "content", "mode": 420}]
    manifest = {"files": [{"path": "main.py", "sha256": "content", "mode": 420, "kind": "file"}],
                "coverage": "complete_declared_scope", "sha256": "endpoint"}
    receipt = {"start": {"content_manifest": manifest}, "end": {"content_manifest": manifest}}
    assert content_match(files, receipt)["complete_scope_match"] is True
    assert content_match([], receipt)["complete_scope_match"] is None
    assert content_match([{**files[0], "sha256": "different"}], receipt)["complete_scope_match"] is False
    assert content_match(files, None)["complete_scope_match"] is None


def test_reader_does_not_require_sqlite(tmp_path):
    from pathlib import Path
    reader = Path(__file__).parents[2] / "lynchpin/sources/chisel_browse.py"
    (tmp_path / "snapshots.json").write_text('{"snapshots": [{"name": "primary"}]}')
    code = '''import builtins, runpy, sys
original = builtins.__import__
def imports(name, *args, **kwargs):
    if name == 'sqlite3': raise ImportError('SQLite absent')
    return original(name, *args, **kwargs)
builtins.__import__ = imports
sys.argv = [sys.argv[1], '--package', sys.argv[2], 'snapshots', '--json']
runpy.run_path(sys.argv[0], run_name='__main__')
'''
    result = subprocess.run(['python', '-I', '-c', code, str(reader), str(tmp_path)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['rows'][0]['name'] == 'primary'
