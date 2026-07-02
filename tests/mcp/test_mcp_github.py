from __future__ import annotations

import pytest


def test_list_github_prs_returns_bounded_compact_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    body = "x" * 500

    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.substrate.github.iter_github_prs",
        lambda *_args, **_kwargs: iter(
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
            ]
        ),
    )

    from lynchpin.mcp.tools.github import list_github_prs

    result = list_github_prs(project="polylogue", state="open", limit=1)

    assert result["total"] == 1
    assert result["limit"] == 1
    assert result["prs"][0]["body_preview"] == body[:240]
    assert result["prs"][0]["body_truncated"] is True
    assert "body" not in result["prs"][0]
    assert "get_github_pr" in result["detail_hint"]

