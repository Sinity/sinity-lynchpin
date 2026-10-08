from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from lynchpin.sources.chisel_compact import compact_jsonl
from lynchpin.sources.chisel_offline import build_offline_package


def _run(package: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""}
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(package / "browse.py"),
            "--package",
            str(package),
            *args,
        ],
        cwd=package,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_offline_package_navigation_and_readonly_sql(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "source" / "src").mkdir(parents=True)
    (package / "history").mkdir()
    (package / "structure").mkdir()
    source = "def one():\n    return 'needle'\n\ndef two():\n    pass\n"
    (package / "source" / "src" / "mod.py").write_text(source)
    (package / "history" / "commits.jsonl").write_text(
        json.dumps({"commit": "abc123", "path": "src/mod.py"}) + "\n"
    )
    (package / "structure" / "symbols.jsonl").write_text(
        json.dumps({"path": "src/mod.py", "name": "one", "start_line": 1}) + "\n"
    )
    (package / "inventory.jsonl").write_text(
        json.dumps({"path": "src/mod.py", "role": "maintained_implementation"}) + "\n"
    )
    (package / "capture.json").write_text(json.dumps({"revision": "abc123"}))

    result = build_offline_package(
        package,
        sqlite=True,
        project="fixture",
        snapshot_id="snap-1",
        generated_at="2026-01-01T00:00:00Z",
    )
    assert result["source_files"] == 1
    assert result["datasets"]["history/commits.jsonl"] == 1
    assert (package / "START_HERE.md").exists()

    search = _run(package, "search", "needle")
    assert (
        search.returncode == 0
        and "src/mod.py" in search.stdout
        and "needle" in search.stdout
    )
    lines = _run(package, "source", "src/mod.py", "--start", "2", "--end", "4")
    assert (
        lines.returncode == 0
        and "return 'needle'" in lines.stdout
        and "def two" in lines.stdout
    )
    hist = _run(package, "history", "--path", "src/mod.py", "--commit", "abc123")
    assert hist.returncode == 0 and "commits.jsonl" in hist.stdout
    select = _run(
        package, "sql", "SELECT dataset, count(*) FROM datasets GROUP BY dataset"
    )
    assert select.returncode == 0 and "history/commits.jsonl" in select.stdout
    mutation = _run(package, "sql", "DELETE FROM datasets")
    assert mutation.returncode == 2


def test_source_helper_rejects_traversal(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "source").mkdir(parents=True)
    build_offline_package(
        package, sqlite=True, project="fixture", snapshot_id="s", generated_at="now"
    )
    result = _run(package, "source", "../outside", "--start", "1", "--end", "1")
    assert result.returncode == 2


def test_compacted_streams_remain_browsable_without_sqlite(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    for area in ("source", "history", "structure", "reports"):
        (package / area).mkdir(parents=True)
    (package / "source" / "main.py").write_text("import helper\n")
    (package / "source" / "structure.jsonl").write_text("source bytes remain direct\n")
    (package / "history" / "commits.jsonl").write_text('{"commit":"abc","message":"historyneedle"}\n')
    (package / "structure" / "dependency_edges.jsonl").write_text('{"from":"main","to":"helper","kind":"python_import"}\n')
    (package / "reports" / "snapshot-differences.jsonl").write_text('{"path":"main.py","snapshot":"worktree"}\n')
    (package / "reports" / "coverage.json").write_text(json.dumps({
        "schema_version": 3,
        "dataset_coverage": {"structure/dependency_edges.jsonl": {"status": "available", "records": 1}},
    }))
    (package / "capture.json").write_text('{"snapshot_id":"primary-id"}')
    compression = compact_jsonl(package)
    assert compression["streams"] == 3
    assert (package / "history/commits.jsonl.gz").is_file()
    assert not (package / "history/commits.jsonl").exists()
    assert (package / "source/structure.jsonl").read_text() == "source bytes remain direct\n"
    coverage = json.loads((package / "reports/coverage.json").read_text())
    assert coverage["schema_version"] == 4
    assert "structure/dependency_edges.jsonl.gz" in coverage["dataset_coverage"]

    result = build_offline_package(
        package, sqlite=False, project="fixture", snapshot_id="primary-id", generated_at="now"
    )
    assert result["datasets"]["history/commits.jsonl.gz"] == 1
    assert not (package / "index.sqlite3").exists()
    assert _run(package, "history", "--commit", "abc").returncode == 0
    neighbors = _run(package, "neighbors", "main", "--json")
    assert neighbors.returncode == 0 and '"to": "helper"' in neighbors.stdout
    differences = _run(package, "differences", "main.py", "--json")
    assert differences.returncode == 0 and '"snapshot": "worktree"' in differences.stdout
    search = _run(package, "search", "historyneedle")
    assert search.returncode == 0 and "commits.jsonl.gz" in search.stdout


def test_evidence_search_source_hash_and_capture_metadata(tmp_path: Path) -> None:
    import hashlib

    package = tmp_path / "evidence"
    source_path = package / "source" / "src" / "api.py"
    source_path.parent.mkdir(parents=True)
    source_text = "class TargetSymbol:\n    pass\n"
    source_path.write_text(source_text)
    for area in ("trackers", "structure"):
        (package / area).mkdir()
    (package / "trackers" / "github.ndjson").write_text(
        json.dumps(
            {
                "kind": "pr",
                "number": 7,
                "review_comments": [
                    {
                        "body": "distinctivereviewtoken on this line",
                        "path": "src/api.py",
                    }
                ],
            }
        )
        + "\n"
    )
    (package / "structure" / "symbols.jsonl").write_text(
        json.dumps(
            {"path": "src/api.py", "qualified_name": "TargetSymbol", "start_line": 1}
        )
        + "\n"
    )
    (package / "inventory.jsonl").write_text(
        json.dumps(
            {
                "path": "src/api.py",
                "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "size_bytes": len(source_text.encode()),
                "included": True,
                "role": "implementation",
            }
        )
        + "\n"
    )
    (package / "capture.json").write_text(
        json.dumps(
            {"revision": "abc987", "dirty": True, "policy_version": "test-policy"}
        )
    )
    (package / "source" / "src" / "sample.jsonl").write_text(
        json.dumps({"text": "sourceonlytoken"}) + "\n"
    )

    result = build_offline_package(
        package, sqlite=True, project="fixture", snapshot_id="snap-evidence", generated_at="now"
    )
    assert "source/src/sample.jsonl" not in result["datasets"]
    guide = (package / "START_HERE.md").read_text()
    assert "abc987" in guide and "True" in guide and "test-policy" in guide

    review = _run(package, "search", "distinctivereviewtoken")
    assert review.returncode == 0 and "trackers/github.ndjson" in review.stdout
    symbol = _run(package, "search", "TargetSymbol")
    assert symbol.returncode == 0 and "structure/symbols.jsonl" in symbol.stdout
    source = _run(package, "source", "source/src/api.py", "--start", "1", "--end", "2")
    assert source.returncode == 0 and "class TargetSymbol" in source.stdout


def test_offline_index_rejects_inventory_hash_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "mismatch"
    (package / "source").mkdir(parents=True)
    (package / "source" / "mod.py").write_text("actual")
    (package / "inventory.jsonl").write_text(
        json.dumps({"path": "mod.py", "sha256": "0" * 64, "included": True}) + "\n"
    )
    try:
        build_offline_package(
            package, sqlite=True, project="fixture", snapshot_id="snap", generated_at="now"
        )
    except ValueError as exc:
        assert "hash differs" in str(exc)
    else:
        raise AssertionError("inventory mismatch should fail package indexing")
