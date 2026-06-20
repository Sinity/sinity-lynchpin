from __future__ import annotations

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
    assert "[1/2] alpha: 1 slices, 7 planned outputs" in output
    assert "[2/2] beta: 2 slices, 8 planned outputs" in output
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
    assert result["status"] == "generated"
