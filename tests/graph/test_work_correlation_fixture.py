"""Mixed-source totals over a fixed, independently specified evidence set."""

import importlib.util
from pathlib import Path


def test_mixed_source_totals_and_graph_relations():
    spec = importlib.util.spec_from_file_location(
        "work_correlation_fixture", Path(__file__).with_name("_work_correlation_fixture.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = module.build_payload()
    rows = payload["correlate_rows"]
    assert sum(row["commit_count"] for row in rows) == 8
    assert sum(row["focus_minutes"] for row in rows) == 420
    assert sum(row["shell_command_count"] for row in rows) == 33
    assert set(payload["summary"]["source_counts"]) == {
        "git", "github", "raw_log", "activitywatch", "terminal"
    }
    graph_rows = payload["graph_rows"]
    assert len(graph_rows) == 5
    assert [row["focus_minutes"] for row in graph_rows] == [60, 70, 80, 90, 100]
    assert all(row["commit_count"] == row["raw_log_count"] == 1 for row in graph_rows)
    relations = payload["dataset_correlations"]
    # Sharing a project and day does not establish a direct relation.
    assert [tuple(row["sources"]) for row in relations] == [("activitywatch", "terminal")]
    assert relations[0]["relation_counts"] == {"temporal_overlap": 5}
