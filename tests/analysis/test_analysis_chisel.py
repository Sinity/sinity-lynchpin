from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lynchpin.sources import chisel
from lynchpin.sources.github import GitHubActor, GitHubItem
from lynchpin.sources.github_context import GitHubContextRow


def _issue(number: int, state: str) -> GitHubContextRow:
    closed_at = datetime(2026, 5, 2, tzinfo=timezone.utc) if state == "closed" else None
    return GitHubContextRow(
        project="example",
        item=GitHubItem(
            repo="example",
            slug="Sinity/example",
            kind="issue",
            number=number,
            title=f"Issue {number}",
            state=state,
            url=f"https://github.com/Sinity/example/issues/{number}",
            author=GitHubActor("Sinity"),
            labels=(),
            body="",
            comments=(),
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            closed_at=closed_at,
        ),
    )


def _pr(number: int, state: str) -> GitHubContextRow:
    merged_at = datetime(2026, 5, 2, tzinfo=timezone.utc) if state == "merged" else None
    return GitHubContextRow(
        project="example",
        item=GitHubItem(
            repo="example",
            slug="Sinity/example",
            kind="pr",
            number=number,
            title=f"PR {number}",
            state=state,
            url=f"https://github.com/Sinity/example/pull/{number}",
            author=GitHubActor("Sinity"),
            labels=(),
            body="",
            comments=(),
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            closed_at=merged_at,
            merged_at=merged_at,
        ),
    )


def test_generate_issues_uses_full_limit_for_open_and_closed(
    monkeypatch, tmp_path: Path
) -> None:
    rows = [
        *[_issue(number, "open") for number in range(1, 3)],
        *[_issue(number, "closed") for number in range(1, 102)],
    ]

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_ensure_github_context_for_chisel", lambda: None)
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("example", "sinity/example", "issue", "open"): [row.item for row in rows if row.item.state == "open"],
            ("example", "sinity/example", "issue", "closed"): [row.item for row in rows if row.item.state == "closed"],
        },
    )

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z") == (2, 101)

    closed_tree = ET.parse(tmp_path / "example-issues-closed.xml")
    assert closed_tree.getroot().attrib["count"] == "101"


def test_generate_issues_does_not_mix_same_slug_aliases(monkeypatch, tmp_path: Path) -> None:
    wanted = _issue(1, "open")
    alias = _issue(2, "open")

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_ensure_github_context_for_chisel", lambda: None)
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("example", "sinity/example", "issue", "open"): [wanted.item],
            ("example-alias", "sinity/example", "issue", "open"): [alias.item],
        },
    )

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z") == (1, 0)
    tree = ET.parse(tmp_path / "example-issues-open.xml")
    numbers = [issue.attrib["number"] for issue in tree.getroot().findall("issue")]
    assert numbers == ["1"]


def test_generate_issues_replaces_stale_open_xml_with_empty_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    stale = tmp_path / "example-issues-open.xml"
    stale.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<issues repository="Sinity/example" state="open" generated-at="old" count="1">
  <issue number="99" state="OPEN" created-at="" updated-at="" url="" />
</issues>""",
        encoding="utf-8",
    )

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_ensure_github_context_for_chisel", lambda: None)
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("example", "sinity/example", "issue", "closed"): [_issue(1, "closed").item],
        },
    )

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z") == (0, 1)
    tree = ET.parse(stale)
    root = tree.getroot()
    assert root.attrib["generated-at"] == "2026-05-24T000000Z"
    assert root.attrib["count"] == "0"
    assert root.findall("issue") == []


def test_generate_prs_replaces_stale_open_xml_with_empty_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    stale = tmp_path / "example-prs-open.xml"
    stale.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<prs repository="Sinity/example" state="open" generated-at="old" count="1">
  <pr number="99" state="OPEN" created-at="" merged-at="" url="" merge-commit="" />
</prs>""",
        encoding="utf-8",
    )

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_ensure_github_context_for_chisel", lambda: None)
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("example", "sinity/example", "pr", "merged"): [_pr(1, "merged").item],
        },
    )

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_prs(plan, tmp_path, "2026-05-24T000000Z") == (0, 1)
    tree = ET.parse(stale)
    root = tree.getroot()
    assert root.attrib["generated-at"] == "2026-05-24T000000Z"
    assert root.attrib["count"] == "0"
    assert root.findall("pr") == []


def test_generate_prs_filters_non_open_items_from_open_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_ensure_github_context_for_chisel", lambda: None)
    monkeypatch.setattr(
        chisel,
        "_github_context_index",
        {
            ("example", "sinity/example", "pr", "open"): [_pr(1, "merged").item],
            ("example", "sinity/example", "pr", "merged"): [_pr(1, "merged").item],
        },
    )

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_prs(plan, tmp_path, "2026-05-24T000000Z") == (0, 1)
    open_root = ET.parse(tmp_path / "example-prs-open.xml").getroot()
    merged_root = ET.parse(tmp_path / "example-prs-merged.xml").getroot()
    assert open_root.attrib["count"] == "0"
    assert open_root.findall("pr") == []
    assert merged_root.attrib["count"] == "1"


def test_generate_beads_exports_issue_dependency_and_memory_context(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = chisel.RepoPlan(name="example", path=repo, slices=())
    exported = [
        {
            "_type": "issue",
            "id": "example-a",
            "title": "Blocked issue",
            "description": "Needs foundation",
            "status": "blocked",
            "priority": 1,
            "issue_type": "feature",
            "assignee": "Sinity",
            "dependencies": ["example-b"],
            "comments": [{"author": "Reviewer", "body": "Still blocked", "created_at": "2026-07-01T00:00:00Z"}],
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
        },
        {
            "_type": "issue",
            "id": "example-b",
            "title": "Foundation",
            "description": "Can start",
            "status": "open",
            "priority": 0,
            "issue_type": "task",
        },
        {"_type": "memory", "id": "mem-1", "text": "Durable context"},
    ]

    def fake_run(cmd, *, cwd=None):
        assert cwd == repo
        if cmd == ["bd", "where", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, chisel.json.dumps({"path": str(repo / ".beads")}), "")
        if cmd == ["bd", "stats", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                chisel.json.dumps({"summary": {"total_issues": 2, "blocked_issues": 1, "ready_issues": 1}}),
                "",
            )
        if cmd == ["bd", "ready", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, chisel.json.dumps([{"id": "example-b"}]), "")
        if cmd == ["bd", "blocked", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, chisel.json.dumps([{"id": "example-a"}]), "")
        if cmd == ["bd", "export", "--include-memories"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                "".join(chisel.json.dumps(row) + "\n" for row in exported),
                "",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(chisel, "_run", fake_run)

    names, size, payload = chisel._generate_beads(plan, tmp_path, "2026-07-03T000000Z")

    assert set(names) == {
        "example-beads.json",
        "example-beads.xml",
        "example-beads.md",
        "example-beads-export.jsonl",
    }
    assert size > 0
    assert payload["available"] is True
    assert payload["counts"]["issues"] == 2
    assert payload["counts"]["memories"] == 1
    assert payload["counts"]["ready"] == 1
    assert payload["counts"]["blocked"] == 1
    assert payload["dependencies"] == [{"issue": "example-a", "depends_on": "example-b", "type": "dependencies"}]

    root = ET.parse(tmp_path / "example-beads.xml").getroot()
    assert root.attrib["ready-count"] == "1"
    blocked_issue = root.find("./issue[@id='example-a']")
    assert blocked_issue is not None
    assert blocked_issue.attrib["blocked"] == "true"
    dependency = blocked_issue.find("dependencies/dependency")
    assert dependency is not None
    assert dependency.attrib["depends-on"] == "example-b"
    markdown = (tmp_path / "example-beads.md").read_text(encoding="utf-8")
    assert "`example-beads-export.jsonl`" in markdown


def test_generate_beads_unavailable_is_nonfatal(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = chisel.RepoPlan(name="example", path=repo, slices=())

    def fake_run(cmd, *, cwd=None):
        return subprocess.CompletedProcess(cmd, 1, "", "no beads workspace")

    monkeypatch.setattr(chisel, "_run", fake_run)

    names, size, payload = chisel._generate_beads(plan, tmp_path, "2026-07-03T000000Z")

    assert names == []
    assert size == 0
    assert payload["available"] is False
    assert not list(tmp_path.glob("example-beads*"))


def test_generate_issues_requires_existing_github_context_product(
    monkeypatch, tmp_path: Path
) -> None:
    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(
        chisel,
        "_ensure_github_context_for_chisel",
        lambda: (_ for _ in ()).throw(
            chisel.MaterializationError(
                "github_context",
                reason="existing GitHub context product is missing",
            )
        ),
    )

    try:
        chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z")
    except chisel.MaterializationError as exc:
        assert exc.product == "github_context"
        assert "existing GitHub context product is missing" in exc.reason
    else:
        raise AssertionError("expected GitHub context to be required")


def test_generate_git_log_names_all_refs_scope(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path / "example",
        slices=(),
    )
    plan.path.mkdir()
    commands: list[list[str]] = []
    log_payload = (
        "\x00abc123\x1fSinity\x1fsinity@example.test\x1f2026-06-01T12:00:00+00:00"
        "\x1forigin/feature/demo\x1ffeat: side branch\x1ffeat: side branch\x1e"
    )

    def fake_run(cmd, *, cwd=None):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, log_payload, "")

    monkeypatch.setattr(chisel, "_run", fake_run)

    count = chisel._generate_git_log(plan, tmp_path, "2026-06-30T000000Z")

    assert count == 1
    assert commands == [[
        "git",
        "log",
        "--all",
        "--reverse",
        "--format=format:%x00%H%x1f%an%x1f%ae%x1f%aI%x1f%D%x1f%s%x1f%B%x1e",
    ]]
    root = ET.parse(tmp_path / "example-git-log-all-refs.xml").getroot()
    assert root.attrib["refs"] == "all"
    assert root.attrib["style"] == "all-refs"


def test_chisel_refreshes_github_context_for_selected_projects(monkeypatch) -> None:
    calls: list[tuple[set[str] | None, bool]] = []

    def fake_materialize_github_context(*, projects=None, progress=None):
        calls.append((projects, progress is not None))
        if progress is not None:
            progress("GitHub context: refreshing alpha")
        return {"row_count": 1}

    monkeypatch.setattr(
        "lynchpin.ingest.github_context_materialize.materialize_github_context",
        fake_materialize_github_context,
    )
    monkeypatch.setattr(chisel, "_build_github_context_index", lambda: {})
    monkeypatch.setattr(chisel, "_github_context_ready", None)
    monkeypatch.setattr(chisel, "_github_context_index", None)

    chisel._ensure_github_context_for_chisel({"alpha", "beta"})

    assert calls == [({"alpha", "beta"}, True)]


def test_chisel_uses_existing_github_context_when_refresh_fails(monkeypatch) -> None:
    calls: list[tuple[set[str] | None, bool]] = []
    printed: list[str] = []
    index = {("example", "sinity/example", "issue", "open"): [_issue(1, "open").item]}

    def fake_materialize_github_context(*, projects=None, progress=None):
        calls.append((projects, progress is not None))
        raise chisel.MaterializationError("github_context", reason="HTTP 502")

    monkeypatch.setattr(
        "lynchpin.ingest.github_context_materialize.materialize_github_context",
        fake_materialize_github_context,
    )
    monkeypatch.setattr(chisel, "_build_github_context_index", lambda: index)
    monkeypatch.setattr(chisel, "_print_live", lambda message="", **_kwargs: printed.append(str(message)))
    monkeypatch.setattr(chisel, "_github_context_ready", None)
    monkeypatch.setattr(chisel, "_github_context_index", None)

    chisel._ensure_github_context_for_chisel({"example"})

    assert calls == [({"example"}, True)]
    assert chisel._github_context_index == index
    assert chisel._github_context_ready is True
    assert any("using existing context product" in line for line in printed)


def test_chisel_reports_missing_existing_github_context_after_refresh_failure(monkeypatch) -> None:
    def fake_materialize_github_context(*, projects=None, progress=None):
        raise chisel.MaterializationError("github_context", reason="HTTP 502")

    monkeypatch.setattr(
        "lynchpin.ingest.github_context_materialize.materialize_github_context",
        fake_materialize_github_context,
    )
    monkeypatch.setattr(
        chisel,
        "_build_github_context_index",
        lambda: (_ for _ in ()).throw(FileNotFoundError("context.ndjson")),
    )
    monkeypatch.setattr(chisel, "_github_context_ready", None)
    monkeypatch.setattr(chisel, "_github_context_index", None)

    try:
        chisel._ensure_github_context_for_chisel({"example"})
    except chisel.MaterializationError as exc:
        assert exc.product == "github_context"
        assert "existing product could not be read" in exc.reason
        assert "context.ndjson" in exc.reason
    else:
        raise AssertionError("expected missing context product to remain fatal")


def test_collect_tokei_stats_buckets_agent_docs_tests_and_other(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = chisel.RepoPlan(
        name="example",
        path=repo,
        slices=(),
        stats_buckets=(
            chisel.StatsBucket("agent-context", "Agent context", (".agent/**",)),
            chisel.StatsBucket(
                "test-suite",
                "Tests",
                ("tests/**", "crate/*/tests/**", *chisel.SINEX_RUST_SPLIT_TEST_PATTERNS),
            ),
            chisel.StatsBucket("docs", "Docs", ("README.md", "docs/**")),
            chisel.StatsBucket("code-proper", "Code", ("src/**", "crate/*/src/**")),
        ),
    )
    split_test = repo / "crate" / "demo" / "src" / "api" / "flow_test.rs"
    split_test.parent.mkdir(parents=True)
    split_test.write_text("fn one() {}\nfn two() {}\n", encoding="utf-8")
    payload = {
        "Markdown": {
            "reports": [
                {"name": str(repo / ".agent" / "README.md"), "stats": {"blanks": 1, "code": 0, "comments": 9}},
                {"name": str(repo / "README.md"), "stats": {"blanks": 2, "code": 0, "comments": 18}},
            ],
        },
        "Rust": {
            "reports": [
                {"name": str(repo / "src" / "lib.rs"), "stats": {"blanks": 3, "code": 30, "comments": 4}},
                {"name": str(repo / "crate" / "demo" / "tests" / "flow.rs"), "stats": {"blanks": 5, "code": 40, "comments": 1}},
                {"name": str(split_test), "stats": {"blanks": 0, "code": 2, "comments": 0}},
            ],
        },
        "JSON": {
            "reports": [
                {"name": str(repo / "schemas" / "event.json"), "stats": {"blanks": 0, "code": 50, "comments": 0}},
            ],
        },
        "Total": {},
    }

    commands: list[list[str]] = []

    def fake_run(cmd, *, cwd=None):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, chisel.json.dumps(payload), "")

    monkeypatch.setattr(chisel, "_run", fake_run)

    stats = chisel._collect_tokei_stats(plan, "2026-06-30T000000Z")

    assert "--no-ignore" in commands[0]
    assert stats["buckets"]["agent-context"]["files"] == 1
    assert stats["buckets"]["agent-context"]["comments"] == 9
    assert stats["buckets"]["docs"]["files"] == 1
    assert stats["buckets"]["test-suite"]["code"] == 42
    assert stats["buckets"]["code-proper"]["code"] == 30
    assert stats["buckets"]["other"]["files"] == 1
    assert stats["buckets"]["other"]["code"] == 50
    assert stats["rust_split_test_files"]["files"] == 1
    assert stats["rust_split_test_files"]["lines"] == 2
    assert stats["rust_inline_tests"]["files"] == 0


def test_tokei_path_normalization_preserves_dot_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = chisel.RepoPlan(name="example", path=repo, slices=())

    assert chisel._relative_tokei_report_name(plan, str(repo / ".agent" / "README.md")) == ".agent/README.md"
    assert chisel._relative_tokei_report_name(plan, "./.agent/README.md") == ".agent/README.md"
    assert chisel._glob_matches(".agent/README.md", ".agent/**")
    assert not chisel._glob_matches("agent/README.md", ".agent/**")


def test_sinex_stats_buckets_classify_agent_separately_from_docs() -> None:
    plan = chisel.REPO_PLANS["sinex"]

    assert chisel._classify_stats_bucket(plan, ".agent/README.md") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".agent/DEVLOOP.md") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".agent/CONVENTIONS.md") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".agent/devloop-contract.json") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".agent/tools/gh_pr_safety.sh") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".github/ci-policy.md") == "agent-instructions"
    assert chisel._classify_stats_bucket(plan, ".agent/conductor-devloop/OPERATING-LOG.md") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/devloops/sinex/reports/summary.md") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/demos/sinex/CURATED_CATALOG.md") == "agent-demos"
    assert chisel._classify_stats_bucket(plan, ".agent/artifacts/sinex/export.json") == "agent-artifacts"
    assert chisel._classify_stats_bucket(plan, "crate/sinexd/tests/api/auth_test.rs") == "test-suite"
    assert chisel._classify_stats_bucket(plan, "crate/sinexd/src/api/handlers/source_status_test.rs") == "test-suite"
    assert chisel._classify_stats_bucket(plan, "crate/sinexd/src/api/replay_control/tests/mod.rs") == "test-suite"
    assert chisel._classify_stats_bucket(plan, "xtask/src/process/tests.rs") == "test-suite"
    assert chisel._classify_stats_bucket(plan, "tests/e2e/README.md") == "test-suite"
    assert chisel._classify_stats_bucket(plan, "crate/sinexd/docs/runtime_qos.md") == "docs"
    assert chisel._classify_stats_bucket(plan, "crate/sinexd/src/main.rs") == "code-sinexd-other"
    assert chisel._classify_stats_bucket(plan, "crate/sinex-db/sql/monitoring.sql") == "code-db"
    assert chisel._classify_stats_bucket(plan, "crate/sinexctl/config.example.toml") == "code-cli"
    assert chisel._classify_stats_bucket(plan, "xtask/build.rs") == "code-xtask"
    assert chisel._classify_stats_bucket(plan, ".config/ast-grep/rules/raw-sqlx-query.yml") == "other-project-surface"
    assert chisel._classify_stats_bucket(plan, "schemas/v2/registry.json") == "other-project-surface"
    assert chisel._classify_stats_bucket(plan, "demo/sinex-recall/recall.sh") == "other-project-surface"
    code_slice = next(slice for slice in plan.slices if slice.name == "code-proper")
    test_slice = next(slice for slice in plan.slices if slice.name == "test-suite")
    agent_devloop_slice = next(slice for slice in plan.slices if slice.name == "agent-devloop")
    agent_demo_slice = next(slice for slice in plan.slices if slice.name == "agent-demos")
    assert set(chisel.SINEX_RUST_SPLIT_TEST_PATTERNS) <= set(code_slice.extra_ignore)
    assert set(chisel.SINEX_RUST_SPLIT_TEST_PATTERNS) <= set(test_slice.include)
    assert ".agent/conductor-devloop/**" in agent_devloop_slice.include
    assert ".agent/demos/**" not in agent_devloop_slice.include
    assert agent_demo_slice.include == (".agent/demos/**",)


def test_polylogue_stats_buckets_split_agent_devloop_and_archive_query() -> None:
    plan = chisel.REPO_PLANS["polylogue"]

    assert chisel._classify_stats_bucket(plan, ".agent/DEVLOOP.md") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/includes/devloop-conventions.md") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/conductor-devloop/RUNBOOK.md") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/task-history/tasks.jsonl") == "agent-devloop"
    assert chisel._classify_stats_bucket(plan, ".agent/demos/chatlog-exports/current/demo/full-chatlog/messages-full.json") == "agent-demo-raw-exports"
    assert chisel._classify_stats_bucket(plan, ".agent/demos/chatlog-exports/current/index.md") == "agent-demos-prompts"
    assert chisel._classify_stats_bucket(plan, ".agent/cloud-prompts/2026-06-22-polylogue-turbo/prompt.md") == "agent-demos-prompts"
    assert chisel._classify_stats_bucket(plan, ".agent/archive/retired-demos/export.jsonl") == "agent-archive"
    assert chisel._classify_stats_bucket(plan, "polylogue/archive/query/parser.py") == "archive-query"
    assert chisel._classify_stats_bucket(plan, "polylogue/archive/session.py") == "archive-data"
    assert chisel._classify_stats_bucket(plan, "polylogue/config.py") == "core-and-storage"
    assert chisel._classify_stats_bucket(plan, "polylogue/publication/__init__.py") == "core-and-storage"
    assert chisel._classify_stats_bucket(plan, "polylogue/scenarios/corpus.py") == "rendering-and-site"
    assert chisel._classify_stats_bucket(plan, "CONTRIBUTING.md") == "docs"
    assert chisel._classify_stats_bucket(plan, "flake.nix") == "devtools-packaging-nix"


def test_agent_audit_classifies_active_transient_and_archive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    agent = repo / ".agent"
    (agent / "includes").mkdir(parents=True)
    (agent / "includes" / "architecture.md").write_text("keep", encoding="utf-8")
    (agent / "archive" / "retired").mkdir(parents=True)
    (agent / "archive" / "retired" / "export.jsonl").write_text("archive", encoding="utf-8")
    (agent / "xtask").mkdir()
    (agent / "xtask" / "tasks.jsonl").write_text("tasks", encoding="utf-8")
    (agent / "scratch" / "current").mkdir(parents=True)
    (agent / "scratch" / "current" / "note.md").write_text("note", encoding="utf-8")

    rows = chisel._agent_audit_rows(agent, repo)
    by_path = {row["path"]: row for row in rows}

    assert by_path[".agent/includes"]["class"] == "active-context"
    assert chisel._agent_audit_class(".agent/DEVLOOP.md")[0] == "active-context"
    assert by_path[".agent/archive"]["class"] == "archive-or-generated"
    assert by_path[".agent/xtask"]["class"] == "review"
    assert chisel._agent_audit_class(".agent/xtask/tasks.jsonl")[0] == "transient-heavy"
    assert chisel._agent_audit_class(".agent/task-history/live-baselines/summary.md")[0] == "active-context"
    assert by_path[".agent/scratch"]["class"] == "scratchpad-managed"
    assert by_path[".agent/scratch/current"]["class"] == "scratchpad-managed"


def test_run_slice_disables_gitignore_for_agent_slices(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(name="example", path=tmp_path, slices=())
    git = {"branch": "main", "commit": "abc123", "dirty": False}
    calls: list[list[str]] = []

    def fake_run_repomix(_bin, output_path, _plan, args, _git, _generated_at, _log=None):
        calls.append(args)
        output_path.write_text("<xml />", encoding="utf-8")
        return output_path.stem, output_path.stat().st_size

    monkeypatch.setattr(chisel, "_run_repomix", fake_run_repomix)

    chisel._run_slice("repomix", tmp_path, plan, chisel.Slice("agent", "Agent", (".agent/demos/**",)), git, "now")
    chisel._run_slice("repomix", tmp_path, plan, chisel.Slice("src", "Source", ("src/**",)), git, "now")

    assert "--no-gitignore" in calls[0]
    assert "--no-gitignore" not in calls[1]


def test_run_scratchpad_uses_curated_include_without_skip_manifest(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scratch = repo / ".agent" / "scratch" / "current"
    scratch.mkdir(parents=True)
    (scratch / "OPERATING-LOG.md").write_text("entry\n", encoding="utf-8")
    plan = chisel.RepoPlan(name="example", path=repo, slices=())
    git = {"branch": "main", "commit": "abc123", "dirty": False}
    calls: list[list[str]] = []

    def fake_run_repomix(_bin, output_path, _plan, args, _git, _generated_at, _log=None):
        calls.append(args)
        output_path.write_text("<xml />", encoding="utf-8")
        return output_path.stem, output_path.stat().st_size

    monkeypatch.setattr(chisel, "_run_repomix", fake_run_repomix)

    result = chisel._run_scratchpad("repomix", tmp_path, plan, git, "now")

    assert result is not None
    assert "--no-gitignore" in calls[0]
    assert "--include" in calls[0]
    assert ",".join(chisel._SCRATCHPAD_INCLUDE) in calls[0]
    assert "--ignore" not in calls[0]
    assert not (tmp_path / "example-scratchpad-skipped.json").exists()


def test_default_ignore_excludes_local_runtime_state() -> None:
    ignored_paths = [
        ".local/share/browser-profile/Preferences",
        ".cache/chromium/blob_storage/data",
        ".lynchpin/duck/substrate.duckdb",
        ".claude/worktrees/agent/crate/demo/src/lib.rs",
        ".serena/cache/rust/index.json",
        ".playwright-mcp/chrome-profile/Default/History",
        ".pytest_cache/v/cache/nodeids",
        ".ruff_cache/0.13.0/cache",
        ".beads/embeddeddolt/repo/.dolt/table_files/chunk",
        "playwright-report/index.html",
        "test-results/e2e/output.json",
        "browser-extension/node_modules/vite/index.js",
        "polylogue/site/dist/assets/app.js",
        "crate/sinexd/target/debug/sinexd",
    ]

    for path in ignored_paths:
        assert chisel._glob_any(path, chisel.DEFAULT_IGNORE), path


def test_generate_snapshot_overview_surfaces_counts_and_attention(tmp_path: Path) -> None:
    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path / "example",
        slices=(chisel.Slice("core", "Core", ("src/**",)),),
        compressed=True,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    large = out_dir / "example-core.xml"
    large.write_text("x" * (chisel.LARGE_SLICE_BYTES + 1), encoding="utf-8")
    (out_dir / "example-prs-open.xml").write_text(
        "<?xml version='1.0'?><prs count='2'></prs>",
        encoding="utf-8",
    )
    (out_dir / "example-issues-open.xml").write_text(
        "<?xml version='1.0'?><issues count='3'></issues>",
        encoding="utf-8",
    )
    (out_dir / "example-branch-delta.patch").write_text("diff", encoding="utf-8")
    (out_dir / "example-tokei-stats.json").write_text(
        chisel.json.dumps({
            "buckets": {
                "core": {"files": 2, "lines": 100, "code": 80, "comments": 5},
            }
        }),
        encoding="utf-8",
    )
    (out_dir / "example-agent-audit.json").write_text(
        chisel.json.dumps({
            "summary_by_class": {
                "review": {"entries": 1, "files": 1, "bytes": 10},
                "archive-or-generated": {"entries": 1, "files": 1, "bytes": 20},
            }
        }),
        encoding="utf-8",
    )
    (out_dir / "example-ignore-audit.json").write_text(
        chisel.json.dumps({"ignored_local_state_bytes": 30, "tracked_hidden_bytes": 40}),
        encoding="utf-8",
    )
    beads = {
        "available": True,
        "counts": {
            "issues": 8,
            "ready": 2,
            "blocked": 1,
            "dependencies": 3,
            "memories": 1,
        },
    }

    names, size = chisel._generate_snapshot_overview(
        plan,
        out_dir,
        "2026-06-11T000000Z",
        {"branch": "main", "commit": "abcdef123456", "dirty": False},
        issues_open=3,
        issues_closed=4,
        prs_open=2,
        prs_merged=5,
        gitlog_commits=6,
        xml_errors=[],
        beads=beads,
    )

    payload = chisel.json.loads((out_dir / "example-overview.json").read_text(encoding="utf-8"))
    markdown = (out_dir / "example-overview.md").read_text(encoding="utf-8")
    assert set(names) == {"example-overview.json", "example-overview.md"}
    assert size > 0
    assert payload["counts"]["open_pr_xml_count"] == 2
    assert payload["counts"]["beads_issues"] == 8
    assert payload["counts"]["beads_ready"] == 2
    assert payload["counts"]["beads_blocked"] == 1
    assert payload["attention"]["agent_review_entries"] == 1
    assert payload["attention"]["beads_blocked"] == 1
    assert "example-beads.md" in payload["open_first"]
    assert payload["attention"]["large_artifacts"][0]["name"] == "example-core.xml"
    assert "`example-prs-open.xml`" in markdown
    assert "| Beads blocked | 1 |" in markdown

    audit_names, audit_size = chisel._generate_snapshot_audit(
        plan,
        out_dir,
        "2026-06-11T000000Z",
        previous_manifest={
            "artifacts": [
                {"name": "example-core.xml", "bytes": chisel.LARGE_SLICE_BYTES - 10},
            ]
        },
    )
    audit = chisel.json.loads((out_dir / "example-snapshot-audit.json").read_text(encoding="utf-8"))
    assert set(audit_names) == {"example-snapshot-audit.json", "example-snapshot-audit.md"}
    assert audit_size > 0
    assert audit["size"]["largest_deltas"][0]["name"] == "example-core.xml"
    assert audit["local_state"]["tracked_hidden_bytes"] == 40
    assert audit["beads"]["issues"] == 8
    assert audit["beads"]["blocked"] == 1


def test_portable_sidecars_name_all_refs_bundle(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path / "example",
        slices=(),
    )
    plan.path.mkdir()
    commands: list[list[str]] = []

    def fake_run(cmd, *, cwd=None):
        commands.append(list(cmd))
        if cmd[:3] == ["git", "bundle", "create"]:
            Path(cmd[3]).write_text("bundle", encoding="utf-8")
        elif cmd and cmd[0] == "tar":
            Path(cmd[2]).write_text("tar", encoding="utf-8")
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(chisel, "_run", fake_run)
    monkeypatch.setattr(chisel, "_repo_tree", lambda *_args, **_kwargs: ".\n")

    sidecars, _size = chisel._generate_portable_sidecars(plan, tmp_path)

    assert "example-all-refs.bundle" in sidecars
    assert "example.bundle" not in sidecars
    assert commands[0] == [
        "git",
        "bundle",
        "create",
        str(tmp_path / "example-all-refs.bundle"),
        "--all",
    ]


def test_build_chisel_bundles_reports_scope_and_grouped_repo_logs(
    monkeypatch, tmp_path: Path
) -> None:
    plan_a = chisel.RepoPlan(
        name="alpha",
        path=tmp_path / "alpha",
        slices=(chisel.Slice("core", "Core", ("src/**",)),),
        compressed=True,
    )
    plan_b = chisel.RepoPlan(
        name="beta",
        path=tmp_path / "beta",
        slices=(
            chisel.Slice("core", "Core", ("src/**",)),
            chisel.Slice("tests", "Tests", ("tests/**",)),
        ),
        compressed=False,
        extra_copy=(("README.md", "README.md"),),
    )
    plan_a.path.mkdir()
    plan_b.path.mkdir()

    printed: list[str] = []

    def fake_build_one(
        plan: chisel.RepoPlan,
        _output_root: Path,
        _repomix_bin: str,
        _generated_at: str,
        slice_workers: int,
    ) -> dict[str, Any]:
        return {
            "project": plan.name,
            "status": "generated",
            "slices": len(plan.slices),
            "issues_open": 0,
            "issues_closed": 0,
            "gitlog_commits": 3,
            "total_bytes": 12,
            "xml_valid": True,
            "elapsed_s": 0.1,
            "log_lines": [
                f"[bold]{plan.name}[/bold] grouped header",
                f"  [green]✓[/green] worker output with {slice_workers} slice workers",
            ],
        }

    monkeypatch.setattr(chisel, "_console", None)
    monkeypatch.setattr(chisel, "_print", lambda message="", **_kwargs: printed.append(str(message)))
    monkeypatch.setattr(chisel, "REPO_PLANS", {"alpha": plan_a, "beta": plan_b})
    monkeypatch.setattr(chisel, "_require_repomix", lambda: "repomix")
    monkeypatch.setattr(chisel, "_repomix_version", lambda _bin: "test-version")
    monkeypatch.setattr(chisel, "_utc_ts", lambda: "2026-06-11T000000Z")
    monkeypatch.setattr(chisel, "_build_one", fake_build_one)

    result = chisel.build_chisel_bundles(output_root=tmp_path / "out", max_workers=8)

    output = "\n".join(printed)
    assert "Repos:  2 selected — alpha, beta" in output
    assert "Pools:  2 across repos × 2 within each; 4 global repomix slots" in output
    assert "[1/2] alpha: 1 configured slices, 5 XML snapshots, 13 sidecars" in output
    assert "[2/2] beta: 2 configured slices, 5 XML snapshots, 14 sidecars" in output
    assert "[1/2]" in output and "[2/2]" in output
    assert "grouped header" in output
    assert "worker output with 2 slice workers" in output
    assert result["projects"]["alpha"]["status"] == "generated"


def test_build_one_emits_live_task_progress(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(
        name="alpha",
        path=tmp_path / "alpha",
        slices=(chisel.Slice("core", "Core", ("src/**",)),),
        compressed=False,
    )
    plan.path.mkdir()
    printed: list[str] = []

    monkeypatch.setattr(chisel, "_console", None)
    monkeypatch.setattr(chisel, "_print", lambda message="", **_kwargs: printed.append(str(message)))
    monkeypatch.setattr(
        chisel,
        "_git_state",
        lambda _path: {"branch": "main", "commit": "abcdef123456", "dirty": False},
    )
    monkeypatch.setattr(chisel, "_run_slice", lambda *_args: ("alpha-core", 10))
    monkeypatch.setattr(chisel, "_run_scratchpad", lambda *_args: None)
    monkeypatch.setattr(chisel, "_generate_git_log", lambda *_args: 2)
    monkeypatch.setattr(chisel, "_generate_issues", lambda *_args: (0, 0))
    monkeypatch.setattr(chisel, "_generate_prs", lambda *_args: (0, 0))
    monkeypatch.setattr(chisel, "_generate_portable_sidecars", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_tokei_stats", lambda *_args: (["alpha-tokei-stats.md"], 8))
    monkeypatch.setattr(chisel, "_generate_ignore_audit", lambda *_args: (["alpha-ignore-audit.md"], 4))
    monkeypatch.setattr(chisel, "_generate_agent_audit", lambda *_args: (["alpha-agent-audit.md"], 3))
    monkeypatch.setattr(chisel, "_generate_branch_delta", lambda *_args: (["alpha-branch-delta.md"], 5))
    monkeypatch.setattr(chisel, "_generate_beads", lambda *_args: (["alpha-beads.md"], 7, {"available": True, "counts": {"issues": 1}}))
    monkeypatch.setattr(chisel, "_generate_snapshot_overview", lambda *_args, **_kwargs: (["alpha-overview.md"], 6))
    monkeypatch.setattr(chisel, "_copy_extras", lambda *_args: 0)
    monkeypatch.setattr(chisel, "_validate_xml", lambda _path: None)
    monkeypatch.setattr(chisel, "_make_combined_tar", lambda *_args: None)

    result = chisel._build_one(
        plan,
        tmp_path / "out",
        "repomix",
        "2026-06-11T000000Z",
        2,
    )

    output = "\n".join(printed)
    assert "→ alpha: start" in output
    assert "→ alpha: slice core" in output
    assert "✓ alpha: slice core" in output
    assert "→ alpha: beads alpha" in output
    assert result["status"] == "generated"
    assert result["beads_files"] == ["alpha-beads.md"]
    assert result["snapshot_audit_files"] == ["alpha-snapshot-audit.json", "alpha-snapshot-audit.md"]


def test_build_one_prunes_stale_project_output(monkeypatch, tmp_path: Path) -> None:
    plan = chisel.RepoPlan(
        name="alpha",
        path=tmp_path / "alpha",
        slices=(),
        compressed=False,
    )
    plan.path.mkdir()
    out_dir = tmp_path / "out" / "alpha"
    out_dir.mkdir(parents=True)
    stale = out_dir / "alpha-old-slice.xml"
    stale.write_text("<old />", encoding="utf-8")

    monkeypatch.setattr(chisel, "_console", None)
    monkeypatch.setattr(chisel, "_print", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        chisel,
        "_git_state",
        lambda _path: {"branch": "main", "commit": "abcdef123456", "dirty": False},
    )
    monkeypatch.setattr(chisel, "_run_scratchpad", lambda *_args: None)
    monkeypatch.setattr(chisel, "_generate_git_log", lambda *_args: 0)
    monkeypatch.setattr(chisel, "_generate_issues", lambda *_args: (0, 0))
    monkeypatch.setattr(chisel, "_generate_prs", lambda *_args: (0, 0))
    monkeypatch.setattr(chisel, "_generate_portable_sidecars", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_tokei_stats", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_ignore_audit", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_agent_audit", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_branch_delta", lambda *_args: ([], 0))
    monkeypatch.setattr(chisel, "_generate_beads", lambda *_args: ([], 0, {"available": False}))
    monkeypatch.setattr(chisel, "_generate_snapshot_overview", lambda *_args, **_kwargs: ([], 0))
    monkeypatch.setattr(chisel, "_copy_extras", lambda *_args: 0)
    monkeypatch.setattr(chisel, "_validate_xml", lambda _path: None)
    monkeypatch.setattr(chisel, "_make_combined_tar", lambda *_args: None)

    result = chisel._build_one(
        plan,
        tmp_path / "out",
        "repomix",
        "2026-06-11T000000Z",
        2,
    )

    assert result["status"] == "generated"
    assert not stale.exists()
    assert (tmp_path / "out" / "alpha" / "alpha-snapshot-audit.json").exists()


def test_write_root_index_surfaces_beads_counts(tmp_path: Path) -> None:
    plan = chisel.RepoPlan(name="alpha", path=tmp_path / "alpha", slices=())
    out_dir = tmp_path / "out" / "alpha"
    out_dir.mkdir(parents=True)
    (out_dir / "alpha-manifest.json").write_text(
        chisel.json.dumps({
            "git": {"branch": "main", "commit": "abc123", "dirty": False},
            "artifacts": [{"name": "alpha-beads.md", "bytes": 10, "scope": "beads-context"}],
        }),
        encoding="utf-8",
    )
    (out_dir / "alpha-tokei-stats.json").write_text(chisel.json.dumps({"buckets": {}}), encoding="utf-8")
    (out_dir / "alpha-overview.json").write_text(
        chisel.json.dumps({
            "counts": {
                "issues_open": 0,
                "prs_open": 0,
                "beads_issues": 4,
                "beads_ready": 2,
                "beads_blocked": 1,
            },
            "attention": {"beads_blocked": 1},
        }),
        encoding="utf-8",
    )
    (out_dir / "alpha-overview.md").write_text("overview", encoding="utf-8")
    (out_dir / "alpha-snapshot-audit.json").write_text(chisel.json.dumps({"beads": {"issues": 4}}), encoding="utf-8")
    (out_dir / "alpha-snapshot-audit.md").write_text("audit", encoding="utf-8")

    chisel._write_root_index(
        tmp_path / "out",
        [plan],
        {"alpha": {"status": "generated"}},
        "2026-07-03T000000Z",
        "repomix-test",
        0.1,
    )

    index = chisel.json.loads((tmp_path / "out" / "index.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "out" / "index.md").read_text(encoding="utf-8")
    counts = index["projects"][0]["overview"]["counts"]
    assert counts["beads_issues"] == 4
    assert "Beads issues" in markdown
    assert "| `alpha` | generated | `main` | false | 0 | 0 | 4 | 2 | 1 |" in markdown
