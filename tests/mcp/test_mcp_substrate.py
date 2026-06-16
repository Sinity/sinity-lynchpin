from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from tests.mcp.conftest import UTC, setup_substrate


def test_query_substrate_select_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("SELECT COUNT(*) AS cnt FROM commit_fact")
    assert result["columns"] == ["cnt"]
    assert result["row_count"] == 1
    assert result["rows"][0][0] == 0
    assert result["truncated"] is False


def test_query_substrate_rejects_drop_table() -> None:
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("DROP TABLE commit_fact")


def test_query_substrate_rejects_insert() -> None:
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("INSERT INTO commit_fact VALUES (1)")


def test_query_substrate_rejects_delete() -> None:
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("DELETE FROM commit_fact WHERE 1=1")


def test_query_substrate_rejects_create() -> None:
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("CREATE TABLE x (id INTEGER)")


def test_query_substrate_tolerates_leading_line_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Header comments are natural in analytical queries. Previously the
    prefix check ran on raw stripped SQL, so any SELECT prefixed with
    ``-- ...`` was rejected as 'disallowed keyword or non-SELECT'."""
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(
        "-- header comment describing the query\n"
        "SELECT COUNT(*) AS cnt FROM commit_fact"
    )
    assert result["row_count"] == 1


def test_query_substrate_tolerates_leading_block_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(
        "/* header\nblock\ncomment */\nWITH cte AS (SELECT 1 AS x) SELECT * FROM cte"
    )
    assert result["row_count"] == 1


def test_query_substrate_still_rejects_disallowed_after_comment() -> None:
    """The comment-skip must not let disallowed statements sneak through."""
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("-- innocent\nDROP TABLE commit_fact")


def test_query_substrate_truncates_at_max_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("SELECT generate_series AS n FROM generate_series(1, 1500)", max_rows=100)
    assert result["truncated"] is True
    assert result["row_count"] == 100
    assert len(result["rows"]) == 100


def test_query_substrate_no_truncation_when_within_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("SELECT generate_series AS n FROM generate_series(1, 10)")
    assert result["truncated"] is False
    assert result["row_count"] == 10


def test_query_substrate_datetime_serialised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("SELECT TIMESTAMPTZ '2026-05-01 12:00:00+00' AS ts")
    val = result["rows"][0][0]
    assert isinstance(val, str)
    assert "2026-05-01" in val


def test_query_substrate_cte_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("WITH t AS (SELECT 42 AS val) SELECT val FROM t")
    assert result["rows"][0][0] == 42


def test_list_substrate_tables_returns_known_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import list_substrate_tables

    tables = list_substrate_tables()
    table_names = {t["table"] for t in tables}

    expected = {
        "commit_fact",
        "file_change_fact",
        "ai_work_event",
        "symbol_change",
        "pr_review_row",
        "evidence_graph_build",
        "evidence_node",
        "evidence_edge",
    }
    assert expected.issubset(table_names)

    for table in tables:
        assert isinstance(table["columns"], list)
        if table["columns"]:
            assert "name" in table["columns"][0]
            assert "type" in table["columns"][0]


def test_list_evidence_graph_builds_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import list_evidence_graph_builds

    assert list_evidence_graph_builds() == []


def test_list_evidence_graph_builds_with_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO evidence_graph_build
                (refresh_id, start_date, end_date, mode, projects,
                 node_count, edge_count, caveats, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "test-rid-001",
                date(2026, 5, 1),
                date(2026, 5, 7),
                "full",
                ["lynchpin"],
                42,
                7,
                "[]",
                datetime(2026, 5, 8, 0, 0, tzinfo=UTC),
            ],
        )

    from lynchpin.mcp.tools.substrate import list_evidence_graph_builds

    result = list_evidence_graph_builds()
    assert len(result) == 1
    assert result[0]["refresh_id"] == "test-rid-001"
    assert result[0]["node_count"] == 42
    assert result[0]["edge_count"] == 7
    assert result[0]["start_date"] == "2026-05-01"


def test_load_evidence_graph_summary_reports_materialization_on_missing_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import load_evidence_graph_summary

    result = load_evidence_graph_summary(start="2026-05-01", end="2026-05-02")

    assert result["error"] == "no matching build"
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["materialization"]["caller"] == "load_evidence_graph_summary"


def test_load_evidence_graph_summary_pinned_refresh_id_does_not_materialize(
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
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        fail_if_materialized,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr("lynchpin.substrate.graph.load_evidence_graph", lambda *_args, **_kwargs: None)

    from lynchpin.mcp.tools.substrate import load_evidence_graph_summary

    result = load_evidence_graph_summary(refresh_id="pinned")

    assert result["error"] == "no matching build"
    assert result["materialization"]["status"] == "pinned"
    assert result["materialization"]["caller"] == "load_evidence_graph_summary"


def test_ai_attribution_backfill_defaults_to_joint_commit_ai_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import ai_attribution_backfill
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO commit_fact (
                sha, repo, project, authored_at, subject,
                lines_added, lines_deleted, lines_changed, files_changed,
                paths, path_roots, breaking_change, categories, change_types,
                classified_files_changed, parent_count, refresh_id, materialized_at
            ) VALUES (?, ?, ?, ?, ?, 1, 0, 1, 1, ?, ?, FALSE, '{}', '{}', 1, 1, ?, ?)
            """,
            [
                "a" * 40,
                "lynchpin",
                "lynchpin",
                datetime(2026, 5, 4, 12, tzinfo=UTC),
                "feat: fixture",
                ["src/a.py"],
                ["src"],
                "joint-rid",
                datetime(2026, 5, 4, 12, tzinfo=UTC),
            ],
        )
        conn.execute(
            """
            INSERT INTO ai_work_event (
                event_id, conversation_id, provider, project, kind,
                kind_confidence, file_paths, tools_used, start_ts,
                duration_ms, refresh_id, materialized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "event-1",
                "conversation-1",
                "claude-code",
                "lynchpin",
                "implementation",
                0.9,
                ["/realm/project/sinity-lynchpin/src/a.py"],
                ["Edit"],
                datetime(2026, 5, 4, 11, tzinfo=UTC),
                1000,
                "joint-rid",
                datetime(2026, 5, 4, 12, tzinfo=UTC),
            ],
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "machine-analysis:latest",
                "machine",
                "ok",
                None,
                1,
                date(2026, 6, 1),
                date(2026, 6, 1),
                datetime(2026, 6, 1, tzinfo=UTC),
            ],
        )

    result = ai_attribution_backfill(dry_run=True)

    assert result["matched_commits"] == 1
    assert result["total_commits"] == 1
    assert result["match_rate"] == 1.0


def test_contract_coverage_ensures_requested_source_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date as _date

    from lynchpin.materialization import MaterializedDataset

    calls = []

    before = MaterializedDataset(
        name="webhistory",
        status="missing",
        authority="test",
        query_surface="test",
        materialized_paths=(),
        raw_roots=(),
        row_count=None,
        first_date=None,
        last_date=None,
        materialization_hint="test",
        reason="missing before materialization",
    )
    after = MaterializedDataset(
        name="webhistory",
        status="ready",
        authority="test",
        query_surface="test",
        materialized_paths=(),
        raw_roots=(),
        row_count=3,
        first_date=_date(2026, 5, 1),
        last_date=_date(2026, 5, 3),
        materialization_hint="test",
        reason="ready after materialization",
    )
    audits = iter([[before], [after]])

    class Result:
        def to_json(self) -> dict[str, object]:
            return {
                "name": "webhistory",
                "status": "updated",
                "changed": True,
                "reason": "materialized",
            }

    def fake_audit_materialization():
        return next(audits)

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", fake_audit_materialization)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    from lynchpin.mcp.tools.substrate import contract_coverage

    rows = contract_coverage(source="webhistory", start="2026-05-01", end="2026-05-03")

    assert calls == [("webhistory", (_date(2026, 5, 1), _date(2026, 5, 4)))]
    assert rows[0]["status"] == "ready"
    assert rows[0]["row_count"] == 3
    assert rows[0]["coverage"]["requested_days"] == 3
    assert rows[0]["coverage"]["covered_days"] == 3
    assert rows[0]["coverage"]["relation"] == "covers_window"
    assert rows[0]["materialization"]["status"] == "updated"


def test_contract_coverage_without_source_is_cheap_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.materialization import MaterializedDataset

    row = MaterializedDataset(
        name="webhistory",
        status="missing",
        authority="test",
        query_surface="test",
        materialized_paths=(),
        raw_roots=(),
        row_count=None,
        first_date=None,
        last_date=None,
        materialization_hint="test",
        reason="missing",
    )

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda: [row])
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected inline materialization")),
    )

    from lynchpin.mcp.tools.substrate import contract_coverage

    rows = contract_coverage(start="2026-05-01", end="2026-05-03")

    assert rows[0]["source"] == "webhistory"
    assert "materialization" not in rows[0]


def test_analysis_claims_materializes_substrate_for_default_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools._utils.require_best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.claims.load_analysis_claims",
        lambda *_args, **_kwargs: [{"claim": "ok"}],
    )

    from lynchpin.mcp.tools.substrate import analysis_claims

    assert analysis_claims(start="2026-05-01", end="2026-05-03") == [{"claim": "ok"}]
    assert calls == [("analysis_claims", (date(2026, 5, 1), date(2026, 5, 4)))]


def test_analysis_claims_includes_end_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.claims import (
        AnalysisClaimRow,
        load_analysis_claims,
        promote_analysis_claims,
    )
    from lynchpin.substrate.connection import connect, substrate_path

    def claim(day: date, summary: str) -> AnalysisClaimRow:
        return AnalysisClaimRow(
            claim_id=f"claim:{summary}",
            claim_type="supported_work",
            project="lynchpin",
            date=day,
            support_level="strong",
            confidence=0.85,
            score=4.2,
            summary=summary,
            source_ids=(),
            relation_ids=(),
            caveats=(),
            payload={},
        )

    with connect(substrate_path()) as conn:
        promote_analysis_claims(
            conn,
            refresh_id="rid-claims",
            claims=[
                claim(date(2026, 5, 1), "before"),
                claim(date(2026, 5, 2), "on-end"),
                claim(date(2026, 5, 3), "after"),
            ],
        )
        rows = load_analysis_claims(
            conn,
            refresh_id="rid-claims",
            start=date(2026, 5, 2),
            end=date(2026, 5, 2),
        )

    assert [row["summary"] for row in rows] == ["on-end"]


def test_claim_evidence_default_snapshot_prefers_broad_claim_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.claims import (
        AnalysisClaimRow,
        load_claim_evidence,
        promote_analysis_claims,
    )
    from lynchpin.substrate.connection import connect, substrate_path

    def claim(refresh_label: str, summary: str, claim_id: str = "claim:shared") -> AnalysisClaimRow:
        return AnalysisClaimRow(
            claim_id=claim_id,
            claim_type="supported_work",
            project="lynchpin",
            date=date(2026, 5, 2),
            support_level="strong",
            confidence=0.85,
            score=4.2,
            summary=summary,
            source_ids=(),
            relation_ids=(),
            caveats=(),
            payload={"refresh_label": refresh_label},
        )

    with connect(substrate_path()) as conn:
        promote_analysis_claims(
            conn,
            refresh_id="broad",
            claims=[
                claim("broad", "broad claim"),
                claim("broad-extra", "extra broad claim", "claim:extra"),
            ],
        )
        promote_analysis_claims(conn, refresh_id="narrow", claims=[claim("narrow", "narrow claim")])
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES
              ('broad', 'analysis_claim', 'source', 'ok', NULL, 10,
               DATE '2026-05-01', DATE '2026-05-31', TIMESTAMPTZ '2026-05-10 00:00:00+00'),
              ('narrow', 'analysis_claim', 'source', 'ok', NULL, 1,
               DATE '2026-05-02', DATE '2026-05-02', TIMESTAMPTZ '2026-05-20 00:00:00+00')
            """
        )
        evidence = load_claim_evidence(conn, claim_id="claim:shared")

    assert evidence is not None
    assert evidence["refresh_id"] == "broad"
    assert evidence["summary"] == "broad claim"


def test_claim_evidence_materializes_for_default_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.substrate.claims.load_claim_evidence",
        lambda *_args, **_kwargs: None,
    )

    from lynchpin.mcp.tools.substrate import claim_evidence

    assert claim_evidence("claim:missing") == {
        "summary": {"status": "missing"},
        "claim_id": "claim:missing",
    }
    assert calls == [("claim_evidence", None)]


def test_readiness_report_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_readiness_report

    result = substrate_readiness_report()
    assert result["latest_materialized_refresh_id"] is None
    assert "latest_refresh_id" not in result
    assert result["sources"] == []
    assert result["evidence_graph"] is None
    assert result["summary"]["trustworthy"] is False
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["materialization"]["status"] in {"blocked", "ready"}


def test_readiness_report_exposes_materialized_snapshot_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, recorded_at)
            VALUES ('rid-fast', 'commits', 'continuous', 'ok', NULL, 1,
                    TIMESTAMPTZ '2026-05-02 00:00:00+00')
            """
        )

    from lynchpin.mcp.tools.substrate import substrate_readiness_report

    result = substrate_readiness_report()

    assert result["latest_materialized_refresh_id"] == "rid-fast"
    assert "latest_refresh_id" not in result
    assert result["materialization"]["source_high_water"]["latest_materialized_refresh_id"] == "rid-fast"


def test_readiness_report_uses_latest_successful_promotion_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_readiness_report
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (
                'rid-full', 'ok', NULL, DATE '2026-05-01', DATE '2026-05-31',
                'materialized', '{"commits":1}', TIMESTAMPTZ '2026-06-05 12:00:00+00',
                TIMESTAMPTZ '2026-06-05 12:01:00+00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
              ('rid-full', 'commits', 'stage', 'ok', NULL, 1,
               DATE '2026-05-01', DATE '2026-05-31', TIMESTAMPTZ '2026-06-05 12:01:00+00'),
              ('rid-narrow', 'machine', 'continuous', 'ok', NULL, 1,
               DATE '2026-06-05', DATE '2026-06-05', TIMESTAMPTZ '2026-06-05 13:00:00+00')
            """
        )

    result = substrate_readiness_report()

    assert result["latest_materialized_refresh_id"] == "rid-full"
    assert result["latest_available_refresh_id"] == "rid-full"
    assert result["latest_available_status"] == "ok"
    assert "latest_refresh_id" not in result
    assert {source["source"] for source in result["sources"]} == {"commits"}


def test_readiness_report_exposes_failed_promotion_as_degraded_available_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_readiness_report
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (
                'rid-failed', 'error', 'activity_content coverage gap',
                DATE '2026-05-01', DATE '2026-05-31', 'materialized',
                '{"evidence_graph_nodes":1}', TIMESTAMPTZ '2026-06-05 12:00:00+00',
                TIMESTAMPTZ '2026-06-05 12:01:00+00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
              ('rid-failed', 'commits', 'stage', 'ok', NULL, 1,
               DATE '2026-05-01', DATE '2026-05-31', TIMESTAMPTZ '2026-06-05 12:01:00+00'),
              ('rid-failed', 'activity_content', 'continuous', 'unavailable',
               'coverage gap', 0, DATE '2026-05-01', DATE '2026-05-31',
               TIMESTAMPTZ '2026-06-05 12:01:00+00')
            """
        )
        conn.execute(
            """
            INSERT INTO evidence_graph_build
            (refresh_id, start_date, end_date, mode, projects, node_count, edge_count, caveats, generated_at)
            VALUES (
                'rid-failed', DATE '2026-05-01', DATE '2026-05-31',
                'materialized', [], 10, 20, '[]',
                TIMESTAMPTZ '2026-06-05 12:00:30+00'
            )
            """
        )

    result = substrate_readiness_report()

    assert result["latest_materialized_refresh_id"] is None
    assert result["latest_available_refresh_id"] == "rid-failed"
    assert result["latest_available_status"] == "error"
    assert result["summary"]["ok"] == 1
    assert result["summary"]["unavailable"] == 1
    assert result["summary"]["trustworthy"] is False
    assert result["materialization"]["status"] == "failed"
    assert result["materialization"]["source_high_water"]["latest_available_refresh_id"] == "rid-failed"
    assert result["evidence_graph"]["refresh_id"] == "rid-failed"


def test_personal_signal_tool_fails_when_backing_stage_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.personal import personal_daily_signals

    with pytest.raises(RuntimeError, match="personal_daily_signals requires substrate table"):
        personal_daily_signals()


def test_promotion_runs_and_analysis_claim_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.claims import AnalysisClaimRow, promote_analysis_claims
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (
                'rid-claims', 'ok', NULL, DATE '2026-05-01', DATE '2026-05-02',
                'materialized', '{"analysis_claims":1}', TIMESTAMPTZ '2026-05-02 00:00:00+00',
                TIMESTAMPTZ '2026-05-02 00:01:00+00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_run_step
            (refresh_id, step, status, message, row_count, recorded_at)
            VALUES (
                'rid-claims', 'analysis_claims', 'ok', 'promoted claims', 1,
                TIMESTAMPTZ '2026-05-02 00:01:00+00'
            )
            """
        )
        promote_analysis_claims(
            conn,
            refresh_id="rid-claims",
            claims=[
                AnalysisClaimRow(
                    claim_id="claim:fixture",
                    claim_type="supported_work",
                    project="lynchpin",
                    date=date(2026, 5, 1),
                    support_level="strong",
                    confidence=0.85,
                    score=4.2,
                    summary="fixture claim",
                    source_ids=(),
                    relation_ids=(),
                    caveats=("fixture caveat",),
                    payload={"source_count": 3},
                )
            ],
        )

    from lynchpin.mcp.tools.substrate import (
        analysis_claim_calibration,
        analysis_claims,
        claim_evidence,
        promotion_runs,
        substrate_run_steps,
    )

    runs = promotion_runs(refresh_id="rid-claims")
    steps = substrate_run_steps(refresh_id="rid-claims")
    claims = analysis_claims(refresh_id="rid-claims")
    calibration = analysis_claim_calibration(refresh_id="rid-claims")
    evidence = claim_evidence("claim:fixture", refresh_id="rid-claims")

    assert runs[0]["status"] == "ok"
    assert steps[0]["step"] == "analysis_claims"
    assert claims[0]["summary"] == "fixture claim"
    assert claims[0]["caveats"] == ["fixture caveat"]
    assert calibration["claim_count"] == 1
    assert calibration["issue_counts"]["missing_evidence_ids"] == 1
    assert evidence["claim_id"] == "claim:fixture"


def test_readiness_report_after_successful_promote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.substrate import substrate_readiness_report
    from lynchpin.substrate.connection import connect, substrate_path

    class Materialization:
        def to_json(self) -> dict[str, object]:
            return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.materialization.substrate_materialization_snapshot",
        lambda *_args, **_kwargs: Materialization(),
    )

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
              ('dag:test-readiness-ok', 'commits', 'stage', 'ok', NULL, 1,
               DATE '2026-05-01', DATE '2026-05-02', TIMESTAMPTZ '2026-05-02 00:00:00+00'),
              ('dag:test-readiness-ok', 'file_changes', 'stage', 'empty', NULL, 0,
               DATE '2026-05-01', DATE '2026-05-02', TIMESTAMPTZ '2026-05-02 00:00:00+00'),
              ('dag:test-readiness-ok', 'symbols', 'stage', 'empty', NULL, 0,
               DATE '2026-05-01', DATE '2026-05-02', TIMESTAMPTZ '2026-05-02 00:00:00+00')
            """
        )

    result = substrate_readiness_report()
    assert result["latest_materialized_refresh_id"] == "dag:test-readiness-ok"
    assert "latest_refresh_id" not in result
    assert result["substrate_version"] is not None
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["materialization"]["status"] == "ready"

    by_source = {s["source"]: s for s in result["sources"]}
    assert by_source["commits"]["kind"] == "stage"
    assert by_source["commits"]["status"] == "ok"
    assert by_source["commits"]["row_count"] == 1
    assert by_source["file_changes"]["status"] == "empty"
    assert by_source["symbols"]["status"] == "empty"

    counts = result["summary"]
    total = counts["ok"] + counts["empty"] + counts["unavailable"] + counts["error"]
    assert total == len(result["sources"])


def test_substrate_consistency_audit_uses_registry_source_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_consistency_audit
    from lynchpin.substrate.connection import connect, substrate_path

    refresh_id = "machine:test"
    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO machine_metric_sample
            (observed_at, host, source, source_schema_version, gap_codes, refresh_id)
            VALUES (?, 'host', 'machine.telemetry', 2, [], ?)
            """,
            [datetime(2026, 6, 5, 12), refresh_id],
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, recorded_at)
            VALUES (?, 'machine', 'stage', 'ok', NULL, 2, ?)
            """,
            [refresh_id, datetime(2026, 6, 5, 13)],
        )

    result = substrate_consistency_audit()

    assert result["checked_count"] == 1
    assert result["discrepancies"][0]["source"] == "machine"
    assert result["discrepancies"][0]["table"] == "machine_metric_sample"
    assert result["discrepancies"][0]["actual_rows"] == 1


def test_substrate_consistency_audit_sums_multi_table_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_consistency_audit
    from lynchpin.substrate.connection import connect, substrate_path

    refresh_id = "work:test"
    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO work_observation (
                source, source_id, work_kind, project, command, started_at,
                ended_at, duration_s, status, host, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:1', 'xtask_invocation', 'sinex',
                ['check'], TIMESTAMPTZ '2026-06-05 12:00:00+00',
                TIMESTAMPTZ '2026-06-05 12:02:00+00', 120.0, 'success',
                'sinnix-prime', ?
            )
            """,
            [refresh_id],
        )
        conn.execute(
            """
            INSERT INTO work_observation_stage (
                source, source_id, invocation_source_id, stage_name,
                started_at, duration_s, success, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:stage:1', 'xtask:live:1', 'clippy',
                TIMESTAMPTZ '2026-06-05 12:00:00+00', 42.0, true, ?
            )
            """,
            [refresh_id],
        )
        conn.execute(
            """
            INSERT INTO work_observation_test_result (
                source, source_id, invocation_source_id, test_name, package,
                status, duration_s, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:test:1', 'xtask:live:1',
                'pkg::test_slow', 'pkg', 'pass', 3.5, ?
            )
            """,
            [refresh_id],
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, recorded_at)
            VALUES (?, 'work_observations', 'stage', 'ok', NULL, 3, ?)
            """,
            [refresh_id, datetime(2026, 6, 5, 13)],
        )

    result = substrate_consistency_audit()

    assert result["checked_count"] == 1
    assert result["trustworthy"] is True
    assert result["discrepancies"] == []


def test_machine_work_observation_tools_read_promoted_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    from lynchpin.substrate.connection import connect

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO work_observation (
                source, source_id, work_kind, project, command, started_at,
                ended_at, duration_s, status, host,
                host_io_pressure_some_avg10_max,
                host_io_pressure_full_avg10_max,
                host_memory_pressure_some_avg10_max,
                process_count_max,
                refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:1', 'xtask_invocation', 'sinex',
                ['check'], TIMESTAMPTZ '2026-05-31 12:00:00+00',
                TIMESTAMPTZ '2026-05-31 12:02:00+00', 120.0, 'success',
                'sinnix-prime', 72.5, 67.7, 12.0, 9, 'r1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_observation (
                source, source_id, work_kind, project, command, started_at,
                ended_at, duration_s, status, exit_code, host, refresh_id
            )
            VALUES (
                'polylogue_devtools', 'polylogue:xtask:1',
                'polylogue_devtools_invocation', 'polylogue',
                ['verify', '--quick'], TIMESTAMPTZ '2026-05-31 12:03:00+00',
                TIMESTAMPTZ '2026-05-31 12:04:00+00', 60.0, 'failed',
                1, 'sinnix-prime', 'r1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_observation_stage (
                source, source_id, invocation_source_id, stage_name,
                started_at, duration_s, success, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:stage:1', 'xtask:live:1', 'clippy',
                TIMESTAMPTZ '2026-05-31 12:00:00+00', 42.0, true, 'r1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_observation_stage (
                source, source_id, invocation_source_id, stage_name,
                started_at, duration_s, success, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:stage:2', 'xtask:live:1', 'compile',
                TIMESTAMPTZ '2026-05-31 12:01:00+00', 84.0, false, 'r1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_observation_test_result (
                source, source_id, invocation_source_id, test_name, package,
                status, duration_s, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:test:1', 'xtask:live:1',
                'pkg::test_slow', 'pkg', 'pass', 3.5, 'r1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_observation_test_result (
                source, source_id, invocation_source_id, test_name, package,
                status, duration_s, refresh_id
            )
            VALUES (
                'xtask_history', 'xtask:live:test:2', 'xtask:live:1',
                'pkg::test_fail', 'pkg', 'fail', 4.5, 'r1'
            )
            """
        )

    from lynchpin.mcp.tools.machine import (
        machine_work_observation_daily,
        machine_work_slow_tests,
        machine_work_stage_daily,
        machine_work_failures,
        machine_work_stage_summary,
        machine_work_test_summary,
        machine_command_performance,
    )

    daily = machine_work_observation_daily(
        project="sinex",
        command_contains="check",
        refresh_id="r1",
    )
    assert daily["summary"]["row_count"] == 1
    assert daily["rows"][0]["observation_count"] == 1
    assert daily["rows"][0]["median_duration_s"] == 120.0

    command_perf = machine_command_performance(
        tool="xtask",
        project="sinex",
        pressure_only=True,
        refresh_id="r1",
    )
    assert command_perf["summary"]["filtered_count"] == 1
    assert {row["tool"] for row in command_perf["summary"]["tool_summaries"]} >= {"xtask"}
    assert command_perf["windows"][0]["source"] == "work_observation"
    assert command_perf["windows"][0]["machine_pressure_state"] == "io_pressure"
    assert command_perf["windows"][0]["host_io_pressure_some_avg10_max"] == 72.5

    polylogue_perf = machine_command_performance(
        tool="polylogue",
        project="polylogue",
        refresh_id="r1",
    )
    assert polylogue_perf["summary"]["filtered_count"] == 1
    assert polylogue_perf["summary"]["tool_summaries"][0]["tool"] == "polylogue"
    assert polylogue_perf["windows"][0]["source_id"] == "polylogue:xtask:1"
    assert polylogue_perf["windows"][0]["machine_work_state"] == "devtools_workload"

    stages = machine_work_stage_summary(stage_name="clippy", refresh_id="r1")
    assert stages["rows"][0]["stage_name"] == "clippy"
    assert stages["rows"][0]["median_duration_s"] == 42.0

    tests = machine_work_test_summary(package="pkg", refresh_id="r1")
    assert tests["rows"][0]["package"] == "pkg"
    assert {row["status"] for row in tests["rows"]} == {"pass", "fail"}

    slow_tests = machine_work_slow_tests(package="pkg", project="sinex", refresh_id="r1")
    assert slow_tests["rows"][0]["test_name"] == "pkg::test_fail"
    assert slow_tests["rows"][0]["duration_s"] == 4.5

    stage_daily = machine_work_stage_daily(
        stage_name="compile",
        project="sinex",
        refresh_id="r1",
    )
    assert stage_daily["rows"][0]["stage_name"] == "compile"
    assert stage_daily["rows"][0]["success_count"] == 0

    failures = machine_work_failures(project="sinex", package="pkg", refresh_id="r1")
    assert failures["rows"][0]["failure_kind"] == "test"
    assert failures["rows"][0]["source_id"] == "xtask:live:test:2"
