from __future__ import annotations

from types import SimpleNamespace

from lynchpin.cli import github_frontier


def test_github_frontier_batches_one_project_at_a_time(monkeypatch):
    items = (
        SimpleNamespace(name="polylogue"),
        SimpleNamespace(name="sinity-lynchpin"),
    )
    calls = []

    monkeypatch.setattr(github_frontier, "active_project_inventory", lambda: items)
    monkeypatch.setattr(github_frontier, "project_github_frontier", lambda batch: calls.append(tuple(batch)) or [batch[0].name])
    monkeypatch.setattr(github_frontier, "github_frontier_summary_markdown", lambda frontiers: "summary:" + ",".join(frontiers))
    monkeypatch.setattr(github_frontier, "github_frontier_markdown", lambda frontiers: "details:" + ",".join(frontiers))

    rendered = github_frontier.render_github_frontier_batch()

    assert calls == [(items[0],), (items[1],)]
    assert "summary:polylogue,sinity-lynchpin" in rendered
    assert "details:polylogue,sinity-lynchpin" in rendered


def test_github_frontier_project_filter_uses_inventory(monkeypatch):
    items = (
        SimpleNamespace(name="polylogue"),
        SimpleNamespace(name="sinity-lynchpin"),
    )
    calls = []

    monkeypatch.setattr(github_frontier, "project_inventory", lambda: items)
    monkeypatch.setattr(github_frontier, "project_github_frontier", lambda batch: calls.append(tuple(batch)) or [batch[0].name])

    rendered = github_frontier.render_github_frontier_batch(projects=["polylogue"], json_output=True)

    assert calls == [(items[0],)]
    assert '"polylogue"' in rendered
    assert "sinity-lynchpin" not in rendered
