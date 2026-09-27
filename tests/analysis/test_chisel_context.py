import json
from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import chisel, chisel_context
from lynchpin.sources.github import (
    GitHubActor,
    GitHubItem,
    GitHubReviewComment,
)


def _pr(number: int, state: str, *, revision: str = "rev-a", dirty: bool = False):
    del revision, dirty
    return GitHubItem(
        repo="Example/project",
        slug="Example/project",
        kind="pr",
        number=number,
        title=f"PR {number}",
        state=state,
        url=f"https://example.test/pull/{number}",
        author=GitHubActor("author"),
        labels=(),
        body="body",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=None,
        merged_at=None,
        review_comments=(
            GitHubReviewComment(
                author=GitHubActor("reviewer"),
                body="Fix this line",
                path="src/main.py",
                line=12,
                diff_hunk="@@ -1 +1 @@",
                created_at=None,
                url="https://example.test/review/1",
                review_id=4,
            ),
        ),
    )


def test_pr_context_covers_all_states_and_serializes_inline_comments(monkeypatch):
    open_pr, closed_pr, merged_pr = _pr(1, "open"), _pr(2, "closed"), _pr(3, "merged")
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("project", "example/project", "pr", "open"): [open_pr],
            ("project", "example/project", "pr", "closed"): [closed_pr],
            ("project", "example/project", "pr", "merged"): [merged_pr],
        },
    )
    for state, expected in (("open", 1), ("closed", 1), ("merged", 1), ("all", 3)):
        rows = chisel._prs_from_context_product("project", "Example/project", state)
        assert len(rows) == expected
        assert all("reviewComments" in row for row in rows)

    row = chisel._prs_from_context_product("project", "Example/project", "closed")
    chisel._normalize_pr_data(row)
    xml = chisel._build_prs_xml(row, "Example/project", "closed", "today")
    assert 'state="CLOSED"' in xml
    assert 'path="src/main.py"' in xml
    assert 'line="12"' in xml
    assert "Fix this line" in xml


def test_context_preserves_missing_dates_and_marks_stale_dirty_evidence(
    tmp_path, monkeypatch
):
    from lynchpin.sources import chisel_options
    monkeypatch.setattr(chisel_options, "active_options", chisel_options.BuildOptions(xml=True))
    monkeypatch.setattr(
        chisel_context,
        "read_native_evidence",
        lambda project: {
            "owner": "agentctl",
            "interface": "agentctl.evidence.list",
            "coverage": "retained_records",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "revision": "digest",
            "rows": [
                {
                    "evidence_id": "e1",
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                    "worker_result": {"candidate_sha": "old-revision"},
                    "candidate": {
                        "candidate_sha": "old-revision",
                        "current_dirty": False,
                    },
                    "verification": [
                        {
                            "claim": {
                                "command": "pytest",
                                "receipt": "agentctl://jobs/1/ref",
                                "tested_sha": "old-revision",
                                "status": "passed",
                                "coverage": {"ac_ids": ["A1"], "scope": "unit"},
                            },
                            "observation": {
                                "observed_at": "2026-01-01T00:00:00+00:00",
                                "checked": True,
                                "eligible": True,
                                "phase": "succeeded",
                                "exit_code": 0,
                                "argv": ["pytest", "-q"],
                            },
                        },
                        {
                            "claim": {
                                "command": "ruff",
                                "receipt": "agentctl://jobs/2/ref",
                                "tested_sha": "new-revision",
                                "status": "passed",
                            },
                            "observation": {
                                "checked": True,
                                "eligible": True,
                                "phase": "succeeded",
                                "exit_code": 0,
                            },
                        },
                    ],
                }
            ],
            "gaps": [],
        },
    )
    monkeypatch.setattr(
        chisel_context,
        "_agentctl_jobs",
        lambda project: {
            "coverage": "all_retained_jobs",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "records": [
                {
                    "source_id": "agentctl:job-1",
                    "operation": "check",
                    "status": "succeeded",
                    "exit_code": 0,
                    "outcome_known": True,
                }
            ],
            "gaps": [],
        },
    )
    project_dir = tmp_path / "package"
    project_dir.mkdir()
    captured_descriptor = project_dir / "source/.agentctl/project.toml"
    captured_descriptor.parent.mkdir(parents=True)
    captured_descriptor.write_text('[project]\nid = "runtime-bar"\n', encoding="utf-8")
    (project_dir / "foo-beads-export.jsonl").write_text(
        '{"id":"task-1"}\n', encoding="utf-8"
    )
    summary = chisel_context.build_context(
        tmp_path,
        project_dir,
        project="foo",
        revision="new-revision",
        dirty=True,
        github_items=[_pr(4, "closed")],
    )
    assert summary["beads_records"] == 1
    assert summary["owner_project"] == "runtime-bar"
    github = (project_dir / "trackers/github.jsonl").read_text(encoding="utf-8")
    assert '"created_at": null' in github
    assert '"state": "closed"' in github
    payload = json.loads(
        (project_dir / "verification/coverage.json").read_text(encoding="utf-8")
    )
    evidence_rows = [
        json.loads(line)
        for line in (project_dir / "verification/records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    checks = evidence_rows[0]["verification"]
    assert checks[0]["applicable_to_package"] is False
    assert checks[1]["applicable_to_package"] is None
    assert payload["captured_package_dirty"] is True
    assert payload["benchmark_exports"] == "unavailable"
    assert evidence_rows[1]["kind"] == "agentctl_job_observation"
    assert evidence_rows[1]["status"] == "succeeded"
    assert "test result" in (project_dir / "verification/README.md").read_text(
        encoding="utf-8"
    )
    assert payload["gaps"]
    assert "Inline review" in (project_dir / "trackers/github.md").read_text(
        encoding="utf-8"
    )


def test_agentctl_job_snapshot_cache_resets_between_builds(monkeypatch):
    reads = []

    def read_snapshot():
        job = len(reads) + 1
        reads.append(job)
        row = SimpleNamespace(
            project="runtime-bar",
            source_id=f"agentctl:{job}",
            operation="check",
            command=(),
            started_at=None,
            ended_at=None,
            duration_s=None,
            status="succeeded",
            exit_code=0,
            outcome_known=True,
            git_commit=None,
            git_dirty=False,
            caveats_json="[]",
        )
        return SimpleNamespace(observations=(row,), caveats=())

    monkeypatch.setattr(
        chisel_context.agentctl, "read_observation_snapshot", read_snapshot
    )
    chisel_context.reset_context_cache()
    first = chisel_context._agentctl_jobs("runtime-bar")
    assert first["records"][0]["source_id"] == "agentctl:1"
    assert (
        chisel_context._agentctl_jobs("runtime-bar")["records"][0]["source_id"]
        == "agentctl:1"
    )
    chisel_context.reset_context_cache()
    second = chisel_context._agentctl_jobs("runtime-bar")
    assert second["records"][0]["source_id"] == "agentctl:2"
    assert reads == [1, 2]


def test_agentctl_job_detail_requires_matching_launch_reference(monkeypatch):
    from types import SimpleNamespace
    import subprocess

    row = SimpleNamespace(source_id="agentctl:12", project="polylogue", operation="verify_quick")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "job_id": 12, "reference": "other-launch",
            "outcome": {"execution_receipt": {"start": {"head": "abc"}}},
        }), "")

    monkeypatch.setattr(chisel_context.subprocess, "run", run)
    assert chisel_context._agentctl_job_detail(row, "expected-launch") is None
    assert calls[0] == ["agentctl", "job", "get", "12", "--reference", "expected-launch", "--json"]


def test_github_actions_runs_keep_owner_facts_and_revision_applicability(tmp_path):
    pages = []

    def runner(command, **kwargs):
        pages.append(command)
        runs = [
            {
                "id": 10,
                "name": "CI",
                "workflow_id": 2,
                "path": ".github/workflows/ci.yml",
                "head_sha": "captured-revision",
                "status": "completed",
                "conclusion": "success",
                "event": "push",
                "created_at": "2026-09-25T10:00:00Z",
                "run_started_at": "2026-09-25T10:01:00Z",
                "updated_at": "2026-09-25T10:05:00Z",
                "html_url": "https://github.test/run/10",
            },
            {
                "id": 9,
                "name": "CI",
                "head_sha": "older-revision",
                "status": "in_progress",
                "conclusion": None,
                "event": "pull_request",
                "created_at": "2026-09-24T10:00:00Z",
                "run_started_at": None,
                "updated_at": "2026-09-24T10:00:00Z",
                "html_url": "https://github.test/run/9",
            },
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(runs), stderr="")

    result = chisel_context.read_ci_runs(
        tmp_path,
        github_slug="Sinity/project",
        revision="captured-revision",
        dirty=False,
        gh_path="gh",
        runner=runner,
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    assert result["coverage"]["coverage"] == "complete_window"
    assert len(pages) == 1
    assert "created=>=2026-06-28" in pages[0][2]
    assert result["records"][0]["applicable_to_package"] is True
    assert result["records"][0]["command"] is None
    assert result["records"][0]["environment"] is None
    assert "not inferred" in result["records"][0]["interpretation"]
    assert result["records"][1]["applicable_to_package"] is False
    assert result["records"][1]["conclusion"] is None


def test_github_actions_runs_reports_cap_and_missing_owner_route(tmp_path, monkeypatch):
    page_calls = []

    def full_page(command, **kwargs):
        page_calls.append(command)
        page = int(command[2].split("page=")[1].split("&")[0])
        rows = [
            {"id": page * 100 + i, "head_sha": "x", "status": "completed"}
            for i in range(100)
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")

    capped = chisel_context.read_ci_runs(
        tmp_path,
        github_slug="Sinity/project",
        revision="x",
        dirty=False,
        gh_path="gh",
        runner=full_page,
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    assert capped["coverage"]["coverage"] == "partial_capped"
    assert capped["coverage"]["record_count"] == 1000
    assert capped["coverage"]["capped"] is True
    assert len(page_calls) == 10

    monkeypatch.setattr(chisel_context.shutil, "which", lambda name: None)
    unavailable = chisel_context.read_ci_runs(
        tmp_path,
        github_slug="Sinity/project",
        revision="x",
        dirty=False,
    )
    assert unavailable["coverage"]["coverage"] == "unavailable"
    assert unavailable["records"] == []
    no_slug = chisel_context.read_ci_runs(
        tmp_path, github_slug=None, revision="x", dirty=False, gh_path="gh"
    )
    assert no_slug["coverage"]["pages_read"] == 0
