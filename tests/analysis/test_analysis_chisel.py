from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lynchpin.analysis.projects import chisel
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


def test_generate_issues_uses_full_limit_for_open_and_closed(
    monkeypatch, tmp_path: Path
) -> None:
    ensure_calls: list[str] = []
    rows = [
        *[_issue(number, "open") for number in range(1, 3)],
        *[_issue(number, "closed") for number in range(1, 102)],
    ]

    def fake_ensure(name: str):
        ensure_calls.append(name)
        return SimpleNamespace(status="ready", reason="fresh fixture")

    def fake_iter_github_context(**_kwargs):
        return iter(rows)

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr("lynchpin.sources.github_context.iter_github_context", fake_iter_github_context)

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z") == (2, 101)
    assert ensure_calls == ["github_context"]

    closed_tree = ET.parse(tmp_path / "example-issues-closed.xml")
    assert closed_tree.getroot().attrib["count"] == "101"
