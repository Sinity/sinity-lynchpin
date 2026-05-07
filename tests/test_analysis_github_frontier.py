from __future__ import annotations

from datetime import date

from lynchpin.analysis.frontier.github_frontier import build_active_github_frontier


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
