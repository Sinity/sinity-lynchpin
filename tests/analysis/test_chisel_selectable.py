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
    unconfigured = task_graph(tasks, [])
    assert unconfigured["campaign_count"] is None
    assert unconfigured["rooted_analysis_status"] == "unconfigured"


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


def test_reader_filters_snapshot_before_pagination_and_refuses_missing_views(tmp_path):
    from lynchpin.sources.chisel_browse import query_records

    (tmp_path / "snapshots.json").write_text(json.dumps({"snapshots": [
        {"name": "primary", "snapshot_id": "primary-id"},
        {"name": "worktree", "snapshot_id": "worktree-id"},
    ]}))
    reports = tmp_path / "reports"
    reports.mkdir()
    records = [
        {"snapshot_id": "primary-id", "evidence_id": None},
        {"snapshot_id": "primary-id", "evidence_id": "primary-1"},
        {"snapshot_id": "primary-id", "evidence_id": "primary-2"},
        {"snapshot_id": "worktree-id", "evidence_id": "worktree-1"},
        {"snapshot_id": "worktree-id", "evidence_id": "worktree-2"},
    ]
    (reports / "candidate-evidence.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    (reports / "references.jsonl").write_text(json.dumps({"snapshot_id": "primary-id", "name": "only_primary"}) + "\n")

    first = query_records(tmp_path, "candidate-evidence", None, 1, 0, "worktree")
    second = query_records(tmp_path, "candidate-evidence", None, 1, 1, "worktree")
    past_end = query_records(tmp_path, "candidate-evidence", None, 1, 2, "primary")
    assert [first["rows"][0]["evidence_id"], second["rows"][0]["evidence_id"]] == ["worktree-1", "worktree-2"]
    assert first["total"] == 2 and second["next_offset"] is None
    assert past_end["rows"] == [] and past_end["total"] == 2
    assert query_records(tmp_path, "references", None, 10, 0, "primary")["total"] == 1

    import pytest
    with pytest.raises(ValueError, match="selected snapshot references are unavailable"):
        query_records(tmp_path, "references", None, 10, 0, "worktree")
    with pytest.raises(ValueError, match="unknown snapshot"):
        query_records(tmp_path, "candidate-evidence", None, 10, 0, "unknown")


def test_candidate_report_keeps_unbound_lifecycle_records_out_of_snapshot_rows(tmp_path):
    from lynchpin.analysis.projects.chisel_reports import build_reports

    (tmp_path / "capture.json").write_text(json.dumps({"snapshot_id": "primary-id", "revision": "commit"}))
    (tmp_path / "snapshots.json").write_text(json.dumps({"snapshots": [
        {"name": "primary", "snapshot_id": "primary-id", "revision": "commit", "dirty": False},
        {"name": "worktree", "snapshot_id": "worktree-id", "revision": "commit", "dirty": True},
    ]}))
    (tmp_path / "source").mkdir()
    overlay = tmp_path / "snapshots/worktree"
    overlay.mkdir(parents=True)
    (overlay / "manifest.json").write_text(json.dumps({"snapshot_id": "worktree-id", "files": [], "changed": [], "deleted": []}))
    verification = tmp_path / "verification"
    verification.mkdir()
    bound_check = {"tested_revision": "commit", "owner_observation": {
        "eligible": True, "execution_receipt": {
            "start": {"head": "commit", "dirty": False},
            "end": {"head": "commit", "dirty": False},
        },
    }}
    records = [
        {"kind": "agentctl_job_observation", "source_id": "job:1", "status": "succeeded"},
        {"kind": "agentctl_job_execution", "source_id": "agentctl:2", "reference": "job-ref",
         "operation": "verify_quick", "phase": "succeeded", "result": "success",
         "execution_evidence": {"selector": ["verify_quick"]},
         "execution_receipt": {"start": {"head": "commit", "dirty": False},
                               "end": {"head": "commit", "dirty": False}}},
        {"kind": "agentctl_job_execution", "source_id": "agentctl:3", "reference": "dirty-job",
         "operation": "verify_quick", "phase": "succeeded", "result": "success",
         "execution_receipt": {"start": {"head": "commit", "dirty": True},
                               "end": {"head": "commit", "dirty": True}}},
        {"kind": "native_evidence", "evidence_id": "bound", "candidate_revision": "commit",
         "candidate_dirty": False, "verification": [bound_check]},
        {"kind": "native_evidence", "evidence_id": "unknown-dirty", "candidate_revision": "commit",
         "candidate_dirty": None, "verification": []},
    ]
    (verification / "records.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))

    build_reports(tmp_path, project="fixture", task_roots=[])

    candidates = [json.loads(line) for line in (tmp_path / "reports/candidate-evidence.jsonl").read_text().splitlines()]
    assert [(row["snapshot_id"], row["evidence_id"]) for row in candidates] == [
        ("primary-id", "bound"), ("primary-id", "job-ref")]
    assert candidates[1]["acceptance"] is None
    assert candidates[1]["execution"]["execution_evidence"]["selector"] == ["verify_quick"]
    coverage = json.loads((tmp_path / "reports/coverage.json").read_text())["candidate_evidence"]
    assert coverage["unbound_native_records"] == 1
    assert coverage["lifecycle_observations_kept_separate"] == 1
    assert coverage["bound_detailed_jobs"] == 1
    assert coverage["unbound_detail_reasons"] == {"complete_content_scope_unavailable": 1}
    report = json.loads((tmp_path / "reports/coverage.json").read_text())
    assert report["dataset_coverage"]["verification/records.jsonl"] == {
        "status": "available", "records": len(records),
    }


def test_candidate_report_explains_same_revision_content_gaps(tmp_path):
    from lynchpin.analysis.projects.chisel_reports import build_reports
    from lynchpin.sources.chisel_browse import query_records

    (tmp_path / "capture.json").write_text(json.dumps({"snapshot_id": "primary-id", "revision": "commit"}))
    (tmp_path / "source").mkdir()
    (tmp_path / "inventory.jsonl").write_text(json.dumps({
        "path": "a.py", "included": True, "sha256": "abc", "mode": 420,
    }) + "\n")
    verification = tmp_path / "verification"
    verification.mkdir()
    def detail(name, files, coverage="complete_declared_scope"):
        manifest = {"files": files, "sha256": name, "coverage": coverage}
        return {"kind": "agentctl_job_execution", "reference": name,
                "execution_receipt": {"start": {"head": "commit", "dirty": True,
                                                 "content_manifest": manifest},
                                      "end": {"head": "commit", "dirty": True,
                                               "content_manifest": manifest}}}
    records = [
        detail("changed", [{"path": "a.py", "kind": "file", "sha256": "different", "mode": 420}]),
        detail("missing", [{"path": "a.py", "kind": "file", "sha256": "abc", "mode": 420},
                           {"path": "b.py", "kind": "file", "sha256": "extra", "mode": 420}]),
        detail("partial", [{"path": "a.py", "kind": "file", "sha256": "abc", "mode": 420}],
               coverage="partial"),
    ]
    (verification / "records.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))

    build_reports(tmp_path, project="fixture", task_roots=[])
    response = query_records(tmp_path, "candidate-evidence", None, 10, 0)
    assert response["rows"] == []
    coverage = response["evidence_coverage"]
    assert coverage["unbound_detail_reasons"] == {
        "complete_content_scope_unavailable": 1,
        "endpoint_content_mismatch": 1,
        "owner_paths_absent_from_capture": 1,
    }
    examples = {row["evidence_id"]: row for row in coverage["unbound_detail_examples"]}
    assert examples["changed"]["mismatched_path_count"] == 1
    assert examples["missing"]["uncaptured_owner_path_count"] == 1


def test_candidate_report_distinguishes_missing_and_empty_evidence_datasets(tmp_path):
    from lynchpin.analysis.projects.chisel_reports import build_reports
    from lynchpin.sources.chisel_browse import query_records

    package = tmp_path / "missing"
    package.mkdir()
    (package / "capture.json").write_text(json.dumps({"snapshot_id": "primary-id"}))
    (package / "source").mkdir()

    build_reports(package, project="fixture", task_roots=[])
    missing = query_records(package, "candidate-evidence", None, 10, 0)["evidence_coverage"]
    assert missing["status"] == "unavailable"
    assert missing["native_records"] is None
    assert missing["lifecycle_observations_kept_separate"] is None
    assert missing["dataset_coverage"]["verification/records.jsonl"] == {
        "status": "unavailable", "records": None,
    }
    assert missing["dataset_coverage"]["inventory.jsonl"] == {
        "status": "unavailable", "records": None,
    }

    verification = package / "verification"
    verification.mkdir()
    (verification / "records.jsonl").write_text("")
    build_reports(package, project="fixture", task_roots=[])
    empty = query_records(package, "candidate-evidence", None, 10, 0)["evidence_coverage"]
    assert empty["status"] == "partial_coverage"
    assert empty["native_records"] == 0
    assert empty["lifecycle_observations_kept_separate"] == 0
    assert empty["dataset_coverage"]["verification/records.jsonl"] == {
        "status": "empty", "records": 0,
    }


def test_neighbor_projection_filters_canonical_edges_before_pagination(tmp_path):
    from lynchpin.sources.chisel_browse import query_records

    (tmp_path / "capture.json").write_text(json.dumps({"snapshot_id": "primary-id"}))
    structure = tmp_path / "structure"
    structure.mkdir()
    edges = [
        {"kind": "python_import", "from": "a", "to": "b", "type_only": True, "deferred": False},
        {"kind": "python_import", "from": "a", "to": "c", "type_only": False, "deferred": True},
        {"kind": "manifest_dependency", "from": "a", "to": "package"},
        {"kind": "python_import", "from": "a", "to": "d", "type_only": False, "deferred": False},
    ]
    (structure / "dependency_edges.jsonl").write_text("".join(json.dumps(row) + "\n" for row in edges))
    all_imports = query_records(tmp_path, "neighbors", "a", 1, 1, projection="all_static_imports")
    eager = query_records(tmp_path, "neighbors", "a", 1, 0, projection="module_initialization")
    assert all_imports["total"] == 3 and all_imports["rows"][0]["to"] == "c"
    assert eager["total"] == 1 and eager["rows"][0]["to"] == "d"
