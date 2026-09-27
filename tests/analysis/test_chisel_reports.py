from __future__ import annotations

from lynchpin.analysis.projects import chisel_build as chisel


def test_beads_history_reports_unplaced_closures_and_current_open_count() -> None:
    issues = [
        {
            "id": "open",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "dated-closed",
            "status": "closed",
            "created_at": "2026-01-01T00:00:00Z",
            "closed_at": "2026-01-02T00:00:00Z",
        },
        {
            "id": "undated-closed",
            "status": "closed",
            "created_at": "2026-01-01T00:00:00Z",
        },
    ]

    history = chisel._beads_history(issues, "2026-01-03T000000Z")
    summary = history["summary"]

    assert summary["estimated_open_from_timestamps"] == 2
    assert summary["open_current_by_status"] == 1
    assert summary["closed_current_without_valid_timestamps"] == 1
    assert summary["closed"] == 1
    assert history["daily"][-1]["estimated_open_from_timestamps"] == 2
    assert history["summary"]["snapshot_day"] == "2026-01-03"
    assert sum(row["closed"] for row in history["daily"]) == 1
