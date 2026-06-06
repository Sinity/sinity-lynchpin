from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import make_commit_entry, setup_substrate, stub_live_promote_sources


def test_project_day_correlations_returns_empty_on_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import project_day_correlations

    assert project_day_correlations() == []


def test_project_day_correlations_returns_dataclass_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)
    stub_live_promote_sources(monkeypatch, tmp_path)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps({"generated_at_utc": "2026-05-08T00:00:00+00:00", "commits": [make_commit_entry("abc" + "0" * 37)]}))

    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_COMMITS,
        run_substrate_promote,
    )

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(tmp_path / "no_fc.json"),
        symbol_changes_file=str(tmp_path / "no_sym.json"),
        sources={SOURCE_COMMITS},
        write_evidence_graph=False,
    )

    from lynchpin.mcp.tools.views import project_day_correlations

    result = project_day_correlations()
    assert isinstance(result, list)
    if result:
        assert {"project", "date", "commit_count", "source_count"}.issubset(result[0])


def test_closure_chain_walks_returns_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import closure_chain_walks

    assert closure_chain_walks() == []


def test_context_pack_diff_defaults_to_successful_promotion_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES
              ('rid-old', 'ok', NULL, DATE '2026-05-01', DATE '2026-05-02',
               'materialized', '{}', TIMESTAMPTZ '2026-06-05 10:00:00+00',
               TIMESTAMPTZ '2026-06-05 10:01:00+00'),
              ('rid-new', 'ok', NULL, DATE '2026-05-02', DATE '2026-05-03',
               'materialized', '{}', TIMESTAMPTZ '2026-06-05 11:00:00+00',
               TIMESTAMPTZ '2026-06-05 11:01:00+00')
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
              ('rid-old', 'commits', 'stage', 'ok', NULL, 0,
               DATE '2026-05-01', DATE '2026-05-02', TIMESTAMPTZ '2026-06-05 10:01:00+00'),
              ('rid-new', 'commits', 'stage', 'ok', NULL, 0,
               DATE '2026-05-02', DATE '2026-05-03', TIMESTAMPTZ '2026-06-05 11:01:00+00'),
              ('rid-narrow', 'machine', 'continuous', 'ok', NULL, 0,
               DATE '2026-06-05', DATE '2026-06-05', TIMESTAMPTZ '2026-06-05 12:00:00+00')
            """
        )

    from lynchpin.mcp.tools.views import context_pack_diff

    result = context_pack_diff()

    assert result["refresh_a"] == "rid-old"
    assert result["refresh_b"] == "rid-new"


def test_project_day_correlations_materializes_substrate_before_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "lynchpin.mcp.tools.views.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.mcp.tools.views.best_materialized_refresh_id", lambda *_args, **_kwargs: "rid")
    monkeypatch.setattr("lynchpin.substrate.derived.load_project_day_correlations", lambda *_args, **_kwargs: [])

    from lynchpin.mcp.tools.views import project_day_correlations

    assert project_day_correlations(start="2026-05-01", end="2026-05-03") == []
    assert calls == [("project_day_correlations", (date(2026, 5, 1), date(2026, 5, 4)))]


def test_project_day_correlations_pinned_refresh_id_does_not_materialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_materialized(*_args, **_kwargs):
        raise AssertionError("explicit refresh_id reads must not converge materialization")

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "lynchpin.mcp.tools.views.ensure_substrate_materialized_for_read",
        fail_if_materialized,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.substrate.derived.load_project_day_correlations", lambda *_args, **_kwargs: [])

    from lynchpin.mcp.tools.views import project_day_correlations

    assert project_day_correlations(refresh_id="pinned") == []


def test_walk_evidence_reports_materialization_when_no_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "lynchpin.mcp.tools.views.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.mcp.tools.views.best_materialized_refresh_id", lambda *_args, **_kwargs: None)

    from lynchpin.mcp.tools.views import walk_evidence

    result = walk_evidence("node:missing")

    assert calls == ["walk_evidence"]
    assert result["reason"] == "no evidence_graph build available"
    assert result["materialization"]["caller"] == "walk_evidence"


def test_walk_evidence_pinned_refresh_id_does_not_materialize(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_materialized(*_args, **_kwargs):
        raise AssertionError("explicit refresh_id reads must not converge materialization")

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "lynchpin.mcp.tools.views.ensure_substrate_materialized_for_read",
        fail_if_materialized,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.substrate.graph.load_evidence_graph", lambda *_args, **_kwargs: None)

    from lynchpin.mcp.tools.views import walk_evidence

    result = walk_evidence("node:missing", refresh_id="pinned")

    assert result["reason"] == "evidence_graph build 'pinned' not found"
    assert result["materialization"]["status"] == "pinned"
