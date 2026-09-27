from __future__ import annotations

import pytest


def test_list_github_prs_returns_bounded_compact_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    body = "x" * 500

    @contextmanager
    def serving(_path):
        yield SimpleNamespace(connection=object(), publication_id="pub-1")

    monkeypatch.setattr("lynchpin.substrate.connection.serving_generation", serving)
    monkeypatch.setattr(
        "lynchpin.substrate.github.iter_github_prs",
        lambda *_args, limit, offset, **_kwargs: iter(
            [
                {
                    "project": "polylogue",
                    "number": 1,
                    "title": "chore: huge dependency PR",
                    "body": body,
                    "state": "open",
                    "url": "https://example.test/pr/1",
                },
                {
                    "project": "polylogue",
                    "number": 2,
                    "title": "chore: second",
                    "body": "small",
                    "state": "open",
                    "url": "https://example.test/pr/2",
                },
            ][offset:offset + limit]
        ),
    )

    from lynchpin.mcp.tools.github import list_github_prs

    result = list_github_prs(project="polylogue", state="open", limit=1)

    assert result["total"] is None
    assert result["returned_count"] == 1
    assert result["truncated"] is True
    assert result["next_offset"] == 1
    assert result["publication_id"] == "pub-1"
    assert result["limit"] == 1
    assert result["prs"][0]["body_preview"] == body[:240]
    assert result["prs"][0]["body_truncated"] is True
    assert "body" not in result["prs"][0]
    assert "get_github_pr" in result["detail_hint"]

    second = list_github_prs(
        project="polylogue", state="open", limit=1, offset=result["next_offset"],
        expected_publication_id=result["publication_id"],
    )
    assert [row["number"] for row in second["prs"]] == [2]
    assert second["truncated"] is False
    from lynchpin.mcp.tools.substrate import QueryPublicationMismatch

    with pytest.raises(QueryPublicationMismatch):
        list_github_prs(
            project="polylogue", limit=1, offset=1,
            expected_publication_id="different-publication",
        )


def test_github_issue_empty_and_exact_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def serving(_path):
        yield SimpleNamespace(connection=object(), publication_id="pub-1")

    rows = [{"project": "lynchpin", "number": 1, "title": "fixture"}]
    monkeypatch.setattr("lynchpin.substrate.connection.serving_generation", serving)
    monkeypatch.setattr(
        "lynchpin.substrate.github.iter_github_issues",
        lambda *_args, project, limit, offset, **_kwargs: iter(
            (rows if project == "lynchpin" else [])[offset:offset + limit]
        ),
    )
    from lynchpin.mcp.tools.github import list_github_issues

    empty = list_github_issues(project="missing", limit=1)
    exact = list_github_issues(project="lynchpin", limit=1)
    assert empty["returned_count"] == 0 and empty["truncated"] is False
    assert exact["returned_count"] == 1 and exact["truncated"] is False
    assert exact["total"] is None
