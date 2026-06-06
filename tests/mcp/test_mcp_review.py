from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import make_pr_dict, setup_substrate


def _fail_if_materialized(*_args, **_kwargs):
    raise AssertionError("explicit refresh_id reads must not converge materialization")


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

    uppercase_results = pr_review_rows(states=["MERGED"])
    assert len(uppercase_results) == 1
    assert uppercase_results[0]["state"] == "merged"

    assert len(pr_review_rows()) == 2


def test_pr_review_rows_defaults_to_best_materialized_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.review import promote_pr_review_rows

    with connect(db_path) as conn:
        promote_pr_review_rows(
            conn,
            rows=[make_pr_dict(project="old", state="merged")],
            refresh_id="old-rid",
        )
        promote_pr_review_rows(
            conn,
            rows=[
                make_pr_dict(project="new-a", state="merged"),
                make_pr_dict(project="new-b", state="open"),
            ],
            refresh_id="new-rid",
        )

    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append(caller)
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.review.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )

    from lynchpin.mcp.tools.review import pr_review_rows

    rows = pr_review_rows()

    assert calls == ["pr_review_rows"]
    assert {row["project"] for row in rows} == {"new-a", "new-b"}


def test_pr_review_rows_pinned_refresh_id_does_not_materialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.review import promote_pr_review_rows

    with connect(db_path) as conn:
        promote_pr_review_rows(
            conn,
            rows=[make_pr_dict(project="lynchpin", state="merged")],
            refresh_id="pinned",
        )

    import lynchpin.mcp.tools.review as review_tools

    monkeypatch.setattr(review_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    rows = review_tools.pr_review_rows(refresh_id="pinned")

    assert len(rows) == 1
    assert rows[0]["project"] == "lynchpin"


def test_pr_review_rows_empty_on_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.review import pr_review_rows

    assert pr_review_rows() == []


def test_review_bottlenecks_materializes_before_snapshot_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append(caller)
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "lynchpin.mcp.tools.review.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.mcp.tools.review.best_materialized_refresh_id", lambda *_args, **_kwargs: None)

    from lynchpin.mcp.tools.review import review_bottlenecks

    assert review_bottlenecks() == []
    assert calls == ["review_bottlenecks"]


def test_review_bottlenecks_pinned_refresh_id_does_not_materialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    import lynchpin.mcp.tools.review as review_tools
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.review import promote_pr_review_rows

    with connect(db_path) as conn:
        row = make_pr_dict(project="lynchpin", state="merged")
        row["review_round_count"] = 3
        promote_pr_review_rows(conn, rows=[row], refresh_id="pinned")

    monkeypatch.setattr(review_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    rows = review_tools.review_bottlenecks(refresh_id="pinned")

    assert len(rows) == 1
    assert rows[0]["project"] == "lynchpin"
