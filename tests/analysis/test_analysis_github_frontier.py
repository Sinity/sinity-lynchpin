from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.analysis.frontier import github_frontier
from lynchpin.analysis.frontier.github_frontier import build_active_github_frontier
from lynchpin.sources.github import GitHubActor, GitHubItem
from lynchpin.sources.github_context import GitHubContextRow


def test_frontier_handles_missing_inputs_gracefully() -> None:
    payload = build_active_github_frontier(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        snapshot_file="/nonexistent/snapshot.json",
        work_packages_file="/nonexistent/packages.json",
    )
    assert payload["window"]["start"] == "2026-05-01"
    assert payload["window"]["end"] == "2026-05-02"
    assert isinstance(payload["projects"], list)
    assert payload["summary"]["available_project_count"] == 0
    assert "methodology" in payload
    assert "lifecycle_source" in payload["methodology"]
    assert "no_scalar_velocity" not in payload
    assert "velocity_score" not in payload


def test_frontier_reads_github_context_product(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"projects": [{"project": "demo", "path": str(repo)}]}),
        encoding="utf-8",
    )
    item = GitHubItem(
        repo="demo",
        slug="Sinity/demo",
        kind="issue",
        number=3,
        title="tracking: materialized frontier",
        state="open",
        url="https://github.com/Sinity/demo/issues/3",
        author=GitHubActor("Sinity"),
        labels=(),
        body="tracking",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=None,
    )
    calls = []
    monkeypatch.setattr(
        github_frontier,
        "ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)) or SimpleNamespace(status="ready"),
    )
    monkeypatch.setattr(
        github_frontier,
        "iter_github_context",
        lambda projects=None, **_kwargs: iter((GitHubContextRow(project="demo", item=item),)),
    )
    monkeypatch.setattr(github_frontier, "_project_kind_mix", lambda *, start, end: {})

    payload = build_active_github_frontier(
        start=date(2026, 5, 1),
        end=date(2026, 5, 7),
        snapshot_file=snapshot,
        work_packages_file="/nonexistent/packages.json",
    )

    assert calls == [("github_context", (date(2026, 5, 1), date(2026, 5, 7)))]
    assert payload["summary"]["available_project_count"] == 1
    assert payload["projects"][0]["tracking_or_horizon_items"][0]["number"] == 3


def test_frontier_reports_unavailable_when_github_context_blocked(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"projects": [{"project": "demo", "path": str(repo)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        github_frontier,
        "ensure_materialized",
        lambda name, *, window=None: SimpleNamespace(status="failed", reason="network_down"),
    )

    payload = build_active_github_frontier(
        start=date(2026, 5, 1),
        end=date(2026, 5, 7),
        snapshot_file=snapshot,
        work_packages_file="/nonexistent/packages.json",
    )

    assert payload["summary"]["available_project_count"] == 0
    assert payload["projects"][0]["status"] == "unavailable"
    assert any("network_down" in caveat for caveat in payload["projects"][0]["caveats"])
