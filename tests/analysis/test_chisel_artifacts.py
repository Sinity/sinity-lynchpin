from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lynchpin.analysis.projects import chisel_build as chisel


def _plan(path: Path) -> chisel.RepoPlan:
    path.mkdir(parents=True, exist_ok=True)
    return chisel.RepoPlan(name="example", path=path, slices=())


def test_agent_audit_class_summaries_count_each_file_once(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scratch = repo / ".agent" / "scratch"
    (scratch / "archive").mkdir(parents=True)
    (scratch / "current").mkdir()
    (scratch / "archive" / "old.jsonl").write_bytes(b"archive")
    (scratch / "current" / "note.md").write_bytes(b"current")
    (repo / ".agent" / "README.md").write_bytes(b"active")

    rows = chisel._agent_audit_rows(repo / ".agent", repo)
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        entry = summary.setdefault(row["class"], {"bytes": 0, "files": 0})
        entry["bytes"] += row["exclusive_bytes"]
        entry["files"] += row["exclusive_files"]

    assert sum(row["exclusive_bytes"] for row in rows) == sum(
        path.stat().st_size for path in (repo / ".agent").rglob("*") if path.is_file()
    )
    assert sum(row["exclusive_files"] for row in rows) == 3
    assert summary["archive-or-generated"] == {"bytes": 7, "files": 1}
    assert summary["scratchpad-managed"] == {"bytes": 7, "files": 1}
    assert summary["active-context"] == {"bytes": 6, "files": 1}


def test_snapshot_counts_actual_xml_and_explicit_pending_artifacts(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("x", encoding="utf-8")
    (out / "example-extra.xml").write_text("<extra />", encoding="utf-8")

    chisel._generate_snapshot_overview(
        plan,
        out,
        "2026-09-26T000000Z",
        {"branch": "main", "commit": "abc", "dirty": False},
        issues_open=0,
        issues_closed=0,
        prs_open=0,
        prs_merged=0,
        gitlog_commits=0,
        xml_errors=[],
        pending_artifact_names=(
            "example-overview.json",
            "example-overview.md",
            "example-snapshot-audit.json",
            "example-snapshot-audit.md",
            "example-manifest.json",
        ),
    )

    overview = json.loads((out / "example-overview.json").read_text())
    assert overview["counts"]["xml_snapshots"] == 1
    assert overview["counts"]["artifacts"] == 7


def test_overview_marks_local_github_counts_and_links_only_existing_artifacts(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    (out / "trackers").mkdir(parents=True)
    (out / "trackers/github-coverage.json").write_text(json.dumps({
        "materialization": {"refresh_status": "local_only", "remote_freshness": "unknown"}
    }))

    chisel._generate_snapshot_overview(
        plan, out, "2026-09-27T000000Z", {"branch": "master", "commit": "abc", "dirty": False},
        issues_open=0, issues_closed=1, prs_open=0, prs_merged=2, gitlog_commits=3,
        xml_errors=[], beads={"available": True, "counts": {"issues": 1}},
        pending_artifact_names=("example-manifest.json",),
    )

    overview = json.loads((out / "example-overview.json").read_text())
    assert overview["counts"]["prs_open_current"] is None
    assert "example-beads.md" not in overview["open_first"]
    assert "example-issues-open.xml" not in overview["open_first"]
    assert "current unavailable; local snapshot 0" in (out / "example-overview.md").read_text()


def test_overview_marks_at_limit_counts_as_unknown_totals(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    (out / "trackers").mkdir(parents=True)
    (out / "trackers/github-coverage.json").write_text(json.dumps({
        "inventory_coverage": {
            "issue": {"coverage": "possibly_truncated", "observed_count": 3,
                      "requested_limit": 3, "total_count": None},
            "pr": {"coverage": "complete", "observed_count": 1,
                   "requested_limit": 3, "total_count": 1},
        },
        "materialization": {"refresh_status": "refreshed"},
    }))

    chisel._generate_snapshot_overview(
        plan, out, "2026-09-27T000000Z", {"branch": "master", "commit": "abc", "dirty": False},
        issues_open=2, issues_closed=1, prs_open=1, prs_merged=0, gitlog_commits=3,
        xml_errors=[],
    )

    overview = json.loads((out / "example-overview.json").read_text())
    counts = overview["counts"]
    assert counts["github_current_count_coverage"] == "possibly_truncated"
    assert counts["issues_open_total"] is None
    assert counts["issues_open_count_semantics"] == "observed_rows_total_unknown"
    assert counts["prs_open_current"] == 1
    report = (out / "example-overview.md").read_text()
    assert "observed rows: 2; current total unknown (inventory limit reached)" in report


def test_snapshot_audit_marks_xml_errors_as_attention(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    out.mkdir()
    chisel._generate_snapshot_overview(
        plan,
        out,
        "2026-09-26T000000Z",
        {"branch": "main", "commit": "abc", "dirty": False},
        issues_open=0,
        issues_closed=0,
        prs_open=0,
        prs_merged=0,
        gitlog_commits=0,
        xml_errors=["broken.xml: invalid token"],
    )

    chisel._generate_snapshot_audit(
        plan,
        out,
        "2026-09-26T000000Z",
        pending_artifact_names=(
            "example-snapshot-audit.json",
            "example-snapshot-audit.md",
            "example-manifest.json",
        ),
    )
    audit = json.loads((out / "example-snapshot-audit.json").read_text())
    assert audit["status"] == "attention"
    assert audit["size"]["artifact_count"] == 5


def test_snapshot_audit_marks_stale_github_fallback(monkeypatch, tmp_path: Path) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(
        chisel,
        "_github_context_manifest",
        {"refresh_status": "stale_fallback", "refresh_error": "HTTP 502"},
    )
    chisel._generate_snapshot_overview(
        plan,
        out,
        "2026-09-26T000000Z",
        {"branch": "main", "commit": "abc", "dirty": False},
        issues_open=0,
        issues_closed=0,
        prs_open=0,
        prs_merged=0,
        gitlog_commits=0,
        xml_errors=[],
    )

    chisel._generate_snapshot_audit(plan, out, "2026-09-26T000000Z")
    audit = json.loads((out / "example-snapshot-audit.json").read_text())
    assert audit["status"] == "attention"
    assert audit["github_context"]["refresh_status"] == "stale_fallback"
    assert audit["github_context"]["refresh_error"] == "HTTP 502"


def test_manifest_self_size_is_stable_and_rerun_has_one_entry(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path / "repo")
    out = tmp_path / "out"
    out.mkdir()
    payload = out / "payload.txt"
    payload.write_text("payload", encoding="utf-8")

    for _ in range(2):
        chisel._write_project_manifest(
            plan,
            out,
            "2026-09-26T000000Z",
            {"branch": "main", "commit": "abc", "dirty": False},
            [],
        )
        manifest_path = out / "example-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        self_rows = [row for row in manifest["artifacts"] if row["name"] == manifest_path.name]
        assert len(self_rows) == 1
        assert self_rows[0]["bytes"] == manifest_path.stat().st_size
        payload_row = next(row for row in manifest["artifacts"] if row["name"] == payload.name)
        assert payload_row["sha256"] == hashlib.sha256(payload.read_bytes()).hexdigest()
