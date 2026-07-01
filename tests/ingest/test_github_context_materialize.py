import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest import github_context_materialize as materializer
from lynchpin.sources.github import (
    GitHubActor,
    GitHubInventoryResult,
    GitHubItem,
    GitHubItemInventory,
    GitHubReviewComment,
)
from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION


def _item(*, repo: str = "lynchpin", kind: str = "issue", number: int = 1) -> GitHubItem:
    return GitHubItem(
        repo=repo,
        slug="Sinity/lynchpin",
        kind=kind,  # type: ignore[arg-type]
        number=number,
        title=f"{kind} {number}",
        state="open",
        url=f"https://github.com/Sinity/lynchpin/{kind}s/{number}",
        author=GitHubActor("Sinity"),
        labels=(),
        body="",
        comments=(),
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        closed_at=None,
    )


def _inventory(
    *,
    repo: str = "lynchpin",
    kind: str = "issue",
    number: int = 1,
    state: str = "open",
) -> GitHubItemInventory:
    return GitHubItemInventory(
        repo=repo,
        slug="Sinity/lynchpin",
        kind=kind,  # type: ignore[arg-type]
        number=number,
        state=state,  # type: ignore[arg-type]
        updated_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        closed_at=datetime(2026, 6, 2, tzinfo=timezone.utc) if state == "closed" else None,
        merged_at=datetime(2026, 6, 2, tzinfo=timezone.utc) if state == "merged" else None,
    )


def test_materialize_github_context_refreshes_open_lists_without_gh_cache(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    inventory_calls_seen: list[tuple[str, bool]] = []
    detail_calls: list[tuple[str, int, bool]] = []
    inventory_calls = 0

    def active_repo_paths():
        nonlocal inventory_calls
        inventory_calls += 1
        return {"lynchpin": repo}

    monkeypatch.setattr(materializer, "_active_repo_paths", active_repo_paths)
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())

    def fetch_issue_inventory(path, *, state, limit, use_cache):
        inventory_calls_seen.append((f"issue:{state}", use_cache))
        return GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (
                _inventory(kind="issue", number=1),
                _inventory(kind="issue", number=2, state="closed"),
            ),
        )

    def fetch_pr_inventory(path, *, state, limit, use_cache):
        inventory_calls_seen.append((f"pr:{state}", use_cache))
        return GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (
                _inventory(kind="pr", number=3),
                _inventory(kind="pr", number=4, state="merged"),
            ),
        )

    def fetch_issue(path, number, **kwargs):
        detail_calls.append(("issue", number, kwargs.get("use_cache")))
        return _item(kind="issue", number=number)

    def fetch_pr(path, number, **kwargs):
        detail_calls.append(("pr", number, kwargs.get("use_cache")))
        return _item(kind="pr", number=number)

    monkeypatch.setattr(materializer, "fetch_issue_inventory", fetch_issue_inventory)
    monkeypatch.setattr(materializer, "fetch_pr_inventory", fetch_pr_inventory)
    monkeypatch.setattr(materializer, "fetch_issue", fetch_issue)
    monkeypatch.setattr(materializer, "fetch_pr", fetch_pr)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    assert inventory_calls_seen == [("issue:all", False), ("pr:all", False)]
    assert detail_calls == [("issue", 1, False), ("issue", 2, False), ("pr", 3, False), ("pr", 4, False)]
    assert inventory_calls == 1
    assert manifest["schema_version"] == GITHUB_CONTEXT_SCHEMA_VERSION
    assert manifest["fetch_status_counts"] == {"ok": 2}
    assert manifest["inventory_items_seen"] == 4
    assert manifest["detail_refreshes"] == 4
    assert manifest["detail_reuses"] == 0
    assert manifest["row_count"] == 4
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {row["kind"] for row in rows} == {"issue", "pr"}


def test_materialize_github_context_reuses_existing_closed_pr_details(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    existing = {
        "project": "lynchpin",
        "repo": "lynchpin",
        "slug": "Sinity/lynchpin",
        "kind": "pr",
        "number": 7,
        "title": "old pr",
        "state": "merged",
        "url": "https://github.com/Sinity/lynchpin/pull/7",
        "author": "Sinity",
        "labels": [],
        "body": "",
        "comments": [{"author": "Sinity", "body": "kept", "created_at": None, "url": None}],
        "reviews": [],
        "review_comments": [{"author": "reviewer", "body": "kept inline", "path": "x.py", "line": 1}],
        "updated_at": "2026-06-02T00:00:00+00:00",
        "merged_at": "2026-06-02T00:00:00+00:00",
    }
    output.write_text(json.dumps(existing) + "\n", encoding="utf-8")
    detail_calls = 0

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(kind="pr", number=7, state="merged"),),
        ),
    )

    def fetch_pr(path, number, **kwargs):
        nonlocal detail_calls
        detail_calls += 1
        return None

    monkeypatch.setattr(materializer, "fetch_pr", fetch_pr)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert detail_calls == 0
    assert manifest["row_count"] == 1
    assert rows[0]["review_comments"][0]["body"] == "kept inline"


def test_materialize_github_context_reuses_existing_open_pr_details(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    existing = {
        "project": "lynchpin",
        "repo": "lynchpin",
        "slug": "Sinity/lynchpin",
        "kind": "pr",
        "number": 7,
        "title": "open pr",
        "state": "open",
        "url": "https://github.com/Sinity/lynchpin/pull/7",
        "author": "Sinity",
        "labels": [],
        "body": "",
        "comments": [],
        "reviews": [],
        "review_comments": [],
        "updated_at": "2026-06-02T00:00:00+00:00",
    }
    output.write_text(json.dumps(existing) + "\n", encoding="utf-8")
    detail_calls = 0

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(kind="pr", number=7, state="open"),),
        ),
    )

    def fetch_pr(path, number, **kwargs):
        nonlocal detail_calls
        detail_calls += 1
        return None

    monkeypatch.setattr(materializer, "fetch_pr", fetch_pr)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert detail_calls == 0
    assert manifest["detail_reuses"] == 1
    assert rows[0]["state"] == "open"


def test_materialize_github_context_hydrates_only_changed_inventory_rows(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    existing_rows = [
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 1,
            "title": "unchanged",
            "state": "closed",
            "updated_at": "2026-06-02T00:00:00+00:00",
            "closed_at": "2026-06-02T00:00:00+00:00",
        },
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 2,
            "title": "old",
            "state": "closed",
            "updated_at": "2026-06-01T00:00:00+00:00",
            "closed_at": "2026-06-01T00:00:00+00:00",
        },
    ]
    output.write_text("\n".join(json.dumps(row) for row in existing_rows) + "\n", encoding="utf-8")
    detail_calls: list[int] = []

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (
                _inventory(number=1, state="closed"),
                GitHubItemInventory(
                    repo="lynchpin",
                    slug="Sinity/lynchpin",
                    kind="issue",
                    number=2,
                    state="closed",
                    updated_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
                    closed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )

    def fetch_issue(path, number, **kwargs):
        detail_calls.append(number)
        return GitHubItem(
            **{
                **_item(number=number).__dict__,
                "state": "closed",
                "title": "new",
                "updated_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
                "closed_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
            }
        )

    monkeypatch.setattr(materializer, "fetch_issue", fetch_issue)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    rows = {
        row["number"]: row
        for row in (json.loads(line) for line in output.read_text(encoding="utf-8").splitlines())
    }
    assert detail_calls == [2]
    assert manifest["detail_reuses"] == 1
    assert manifest["detail_refreshes"] == 1
    assert rows[1]["title"] == "unchanged"
    assert rows[2]["title"] == "new"


def test_materialize_github_context_refreshes_closed_lists_when_history_exists(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    rows = [
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 1,
            "state": "closed",
        },
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "pr",
            "number": 2,
            "state": "merged",
            "review_comments": [{"body": "kept"}],
        },
    ]
    output.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())

    def fetch_issues(path, *, state, limit, use_cache):
        calls.append(("issue", state))
        return GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ())

    def fetch_prs(path, *, state, limit, use_cache):
        calls.append(("pr", state))
        return GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ())

    monkeypatch.setattr(materializer, "fetch_issue_inventory", fetch_issues)
    monkeypatch.setattr(materializer, "fetch_pr_inventory", fetch_prs)

    materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    assert calls == [("issue", "all"), ("pr", "all")]


def test_materialize_github_context_filters_projects(monkeypatch, tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    output = tmp_path / "github" / "context.ndjson"
    calls: list[str] = []

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"alpha": repo_a, "beta": repo_b})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: f"Sinity/{path.name}")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())

    def fetch_issues(path, *, state, limit, use_cache):
        calls.append(f"issue:{path.name}:{state}")
        return GitHubInventoryResult("ok", path.name, f"Sinity/{path.name}", ())

    def fetch_prs(path, *, state, limit, use_cache):
        calls.append(f"pr:{path.name}:{state}")
        return GitHubInventoryResult("ok", path.name, f"Sinity/{path.name}", ())

    monkeypatch.setattr(materializer, "fetch_issue_inventory", fetch_issues)
    monkeypatch.setattr(materializer, "fetch_pr_inventory", fetch_prs)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
        projects={"alpha"},
    )

    assert calls == ["issue:repo-a:all", "pr:repo-a:all"]
    assert manifest["project_counts"] == {}


def test_materialize_github_context_drops_stale_open_rows(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    rows = [
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 1,
            "state": "open",
        },
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 2,
            "state": "open",
        },
            {
                "project": "lynchpin",
                "repo": "lynchpin",
                "slug": "Sinity/lynchpin",
                "kind": "issue",
                "number": 3,
                "state": "closed",
                "updated_at": "2026-06-02T00:00:00+00:00",
                "closed_at": "2026-06-02T00:00:00+00:00",
            },
    ]
    output.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(number=2), _inventory(number=3, state="closed")),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(materializer, "fetch_issue", lambda path, number, **kwargs: _item(number=number))

    materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    by_number = {
        row["number"]: row["state"]
        for row in (json.loads(line) for line in output.read_text(encoding="utf-8").splitlines())
        if row["kind"] == "issue"
    }
    assert by_number == {2: "open", 3: "closed"}


def test_materialize_github_context_drops_stale_open_pr_rows(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    rows = [
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "pr",
            "number": 1,
            "state": "open",
        },
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "pr",
            "number": 2,
            "state": "open",
        },
    ]
    output.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(kind="pr", number=2),),
        ),
    )
    monkeypatch.setattr(materializer, "fetch_pr", lambda path, number, **kwargs: _item(kind="pr", number=number))

    materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    by_number = {
        row["number"]: row["state"]
        for row in (json.loads(line) for line in output.read_text(encoding="utf-8").splitlines())
        if row["kind"] == "pr"
    }
    assert by_number == {2: "open"}


def test_materialize_github_context_does_not_refetch_existing_commit_refs(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    rows = [
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "pr",
            "number": 7,
            "state": "merged",
        },
        {
            "project": "lynchpin",
            "repo": "lynchpin",
            "slug": "Sinity/lynchpin",
            "kind": "issue",
            "number": 8,
            "state": "closed",
        },
    ]
    output.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    detail_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(
        materializer,
        "commit_facts",
        lambda *, start, end, all_refs, include_paths: (
            type("Fact", (), {"repo": "lynchpin", "subject": "fix thing (#7)\n\nRefs #8"})(),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr",
        lambda path, number, **kwargs: detail_calls.append(("pr", number)) or None,
    )
    monkeypatch.setattr(
        materializer,
        "fetch_issue",
        lambda path, number, **kwargs: detail_calls.append(("issue", number)) or None,
    )

    materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    assert detail_calls == []


def test_materialize_github_context_caps_missing_commit_ref_fetches(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    detail_calls: list[int] = []

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(
        materializer,
        "commit_facts",
        lambda *, start, end, all_refs, include_paths: (
            type("Fact", (), {"repo": "lynchpin", "subject": "one (#1)"})(),
            type("Fact", (), {"repo": "lynchpin", "subject": "two (#2)"})(),
            type("Fact", (), {"repo": "lynchpin", "subject": "three (#3)"})(),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(materializer, "fetch_pr", lambda path, number, **kwargs: detail_calls.append(number) or None)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
        commit_ref_fetch_limit=2,
    )

    assert detail_calls == [1, 2]
    assert manifest["missing_commit_refs_seen"] == 3
    assert manifest["missing_commit_refs_attempted"] == 2
    assert manifest["missing_commit_refs_deferred"] == 1


def test_materialize_github_context_backfills_commit_refs_from_all_branches(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    calls: list[tuple[bool, bool]] = []

    def fake_commit_facts(*, start, end, all_refs, include_paths):
        calls.append((all_refs, include_paths))
        return (type("Fact", (), {"repo": "lynchpin", "subject": "side branch work (#9)"})(),)

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", fake_commit_facts)
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr",
        lambda path, number, **kwargs: _item(kind="pr", number=number),
    )

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    assert calls == [(True, False)]
    assert manifest["missing_commit_refs_fetched"] == 1
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [(row["kind"], row["number"]) for row in rows] == [("pr", 9)]


def test_materialize_github_context_preserves_previous_product_on_network_failure(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    manifest_path = output.with_suffix(".manifest.json")
    output.write_text('{"project": "old"}\n', encoding="utf-8")
    manifest_path.write_text('{"row_count": 1}\n', encoding="utf-8")

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())

    def fetch_issues(path, *, state, limit, use_cache):
        status = "error"
        reason = "network_down" if status == "error" else None
        return GitHubInventoryResult(status, "lynchpin", "Sinity/lynchpin", (), reason)  # type: ignore[arg-type]

    def fetch_prs(path, *, state, limit, use_cache):
        return GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ())

    monkeypatch.setattr(materializer, "fetch_issue_inventory", fetch_issues)
    monkeypatch.setattr(materializer, "fetch_pr_inventory", fetch_prs)

    with pytest.raises(MaterializationError, match="network_down"):
        materializer.materialize_github_context(
            output=output,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
            end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
        )

    assert output.read_text(encoding="utf-8") == '{"project": "old"}\n'
    assert manifest_path.read_text(encoding="utf-8") == '{"row_count": 1}\n'


def test_materialize_github_context_preserves_previous_product_on_detail_miss(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    manifest_path = output.with_suffix(".manifest.json")
    output.write_text('{"project": "old"}\n', encoding="utf-8")
    manifest_path.write_text('{"row_count": 1}\n', encoding="utf-8")

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(number=10),),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(materializer, "fetch_issue", lambda path, number, **kwargs: None)

    with pytest.raises(MaterializationError, match="detail refresh failed"):
        materializer.materialize_github_context(
            output=output,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
            end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
        )

    assert output.read_text(encoding="utf-8") == '{"project": "old"}\n'
    assert manifest_path.read_text(encoding="utf-8") == '{"row_count": 1}\n'


def test_materialize_github_context_enriches_pr_review_comments(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    pr = _item(kind="pr", number=7)
    enriched = GitHubItem(
        **{
            **pr.__dict__,
            "review_comments": (
                GitHubReviewComment(
                    author=GitHubActor("reviewer"),
                    body="inline",
                    path="lynchpin/materialization.py",
                    line=12,
                    diff_hunk="@@",
                    created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
                    review_id=99,
                ),
            ),
        }
    )

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, all_refs, include_paths: ())
    monkeypatch.setattr(
        materializer,
        "fetch_issue_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult("ok", "lynchpin", "Sinity/lynchpin", ()),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr_inventory",
        lambda path, *, state, limit, use_cache: GitHubInventoryResult(
            "ok",
            "lynchpin",
            "Sinity/lynchpin",
            (_inventory(kind="pr", number=7),),
        ),
    )
    monkeypatch.setattr(
        materializer,
        "fetch_pr",
        lambda path, number, **kwargs: enriched if kwargs.get("include_review_comments") else pr,
    )

    materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["review_comments"][0]["path"] == "lynchpin/materialization.py"
