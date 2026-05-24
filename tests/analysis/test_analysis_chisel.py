from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from lynchpin.analysis.projects import chisel


def _issue(number: int, state: str) -> dict:
    return {
        "number": number,
        "state": state.upper(),
        "title": f"Issue {number}",
        "body": "",
        "labels": [],
        "url": f"https://github.com/Sinity/example/issues/{number}",
        "createdAt": "2026-05-01T00:00:00Z",
        "updatedAt": "2026-05-01T00:00:00Z",
        "closedAt": "2026-05-02T00:00:00Z" if state == "closed" else None,
        "comments": [],
    }


def test_generate_issues_uses_full_limit_for_open_and_closed(
    monkeypatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, int]] = []

    def fake_fetch_issues(
        repo_slug: str, state: str, limit: int, repo_path: Path
    ) -> list[dict]:
        seen.append((state, limit))
        count = 2 if state == "open" else 101
        return [_issue(number, state) for number in range(1, count + 1)]

    monkeypatch.setattr(chisel, "_has_github_remote", lambda repo: True)
    monkeypatch.setattr(chisel, "_fetch_issues", fake_fetch_issues)

    plan = chisel.RepoPlan(
        name="example",
        path=tmp_path,
        slices=(),
        github_slug="Sinity/example",
    )

    assert chisel._generate_issues(plan, tmp_path, "2026-05-24T000000Z") == (2, 101)
    assert seen == [
        ("open", chisel.DEFAULT_ISSUE_LIMIT),
        ("closed", chisel.DEFAULT_ISSUE_LIMIT),
    ]

    closed_tree = ET.parse(tmp_path / "issues-closed.xml")
    assert closed_tree.getroot().attrib["count"] == "101"
