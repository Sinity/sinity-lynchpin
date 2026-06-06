import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest import github_context_materialize as materializer
from lynchpin.sources.github import GitHubActor, GitHubFetchResult, GitHubItem, GitHubReviewComment
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


def test_materialize_github_context_refreshes_network_not_gh_cache(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    calls: list[tuple[str, bool]] = []
    inventory_calls = 0

    def active_repo_paths():
        nonlocal inventory_calls
        inventory_calls += 1
        return {"lynchpin": repo}

    monkeypatch.setattr(materializer, "_active_repo_paths", active_repo_paths)
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, include_paths: ())

    def fetch_issues(path, *, state, limit, use_cache):
        calls.append((f"issue:{state}", use_cache))
        return GitHubFetchResult("ok", "lynchpin", "Sinity/lynchpin", (_item(number=len(calls)),))

    def fetch_prs(path, *, state, limit, use_cache):
        calls.append((f"pr:{state}", use_cache))
        return GitHubFetchResult("ok", "lynchpin", "Sinity/lynchpin", (_item(kind="pr", number=len(calls)),))

    monkeypatch.setattr(materializer, "fetch_issues", fetch_issues)
    monkeypatch.setattr(materializer, "fetch_prs", fetch_prs)
    monkeypatch.setattr(materializer, "fetch_pr", lambda path, number, **kwargs: None)

    manifest = materializer.materialize_github_context(
        output=output,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc).date(),
        end=datetime(2026, 6, 3, tzinfo=timezone.utc).date(),
    )

    assert {use_cache for _, use_cache in calls} == {False}
    assert inventory_calls == 1
    assert manifest["schema_version"] == GITHUB_CONTEXT_SCHEMA_VERSION
    assert manifest["fetch_status_counts"] == {"ok": 4}
    assert manifest["row_count"] == 4
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {row["kind"] for row in rows} == {"issue", "pr"}


def test_materialize_github_context_preserves_previous_product_on_network_failure(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "github" / "context.ndjson"
    output.parent.mkdir()
    manifest_path = output.with_suffix(".manifest.json")
    output.write_text('{"project": "old"}\n', encoding="utf-8")
    manifest_path.write_text('{"row_count": 1}\n', encoding="utf-8")

    monkeypatch.setattr(materializer, "_active_repo_paths", lambda: {"lynchpin": repo})
    monkeypatch.setattr(materializer, "repo_slug", lambda path: "Sinity/lynchpin")
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, include_paths: ())

    def fetch_issues(path, *, state, limit, use_cache):
        status = "error" if state == "open" else "ok"
        reason = "network_down" if status == "error" else None
        return GitHubFetchResult(status, "lynchpin", "Sinity/lynchpin", (), reason)  # type: ignore[arg-type]

    def fetch_prs(path, *, state, limit, use_cache):
        return GitHubFetchResult("ok", "lynchpin", "Sinity/lynchpin", ())

    monkeypatch.setattr(materializer, "fetch_issues", fetch_issues)
    monkeypatch.setattr(materializer, "fetch_prs", fetch_prs)

    with pytest.raises(MaterializationError, match="network_down"):
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
    monkeypatch.setattr(materializer, "commit_facts", lambda *, start, end, include_paths: ())
    monkeypatch.setattr(materializer, "fetch_issues", lambda path, *, state, limit, use_cache: GitHubFetchResult("ok", "lynchpin", "Sinity/lynchpin", ()))
    monkeypatch.setattr(materializer, "fetch_prs", lambda path, *, state, limit, use_cache: GitHubFetchResult("ok", "lynchpin", "Sinity/lynchpin", (pr,) if state == "open" else ()))
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
