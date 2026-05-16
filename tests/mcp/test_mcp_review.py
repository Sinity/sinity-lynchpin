from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import make_pr_dict, setup_substrate


def test_pr_review_rows_filters_by_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.review import promote_pr_review_rows

    with connect(db_path) as conn:
        promote_pr_review_rows(
            conn,
            rows=[
                make_pr_dict(project="lynchpin", state="merged"),
                make_pr_dict(project="sinex", state="open"),
            ],
            refresh_id="test-pr-001",
        )

    from lynchpin.mcp.tools.review import pr_review_rows

    merged_results = pr_review_rows(states=["merged"])
    assert len(merged_results) == 1
    assert merged_results[0]["state"] == "merged"
    assert merged_results[0]["project"] == "lynchpin"

    open_results = pr_review_rows(states=["open"])
    assert len(open_results) == 1
    assert open_results[0]["state"] == "open"
    assert open_results[0]["project"] == "sinex"

    assert len(pr_review_rows()) == 2


def test_pr_review_rows_empty_on_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.review import pr_review_rows

    assert pr_review_rows() == []
