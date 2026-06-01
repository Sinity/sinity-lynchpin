from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.mcp.conftest import UTC, make_commit_entry, setup_substrate, stub_live_promote_sources


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


def test_readiness_report_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_readiness_report

    result = substrate_readiness_report()
    assert result["latest_refresh_id"] is None
    assert result["sources"] == []
    assert result["evidence_graph"] is None
    assert result["summary"]["trustworthy"] is False


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
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    from tests.mcp.conftest import reload_config

    reload_config(monkeypatch)
    stub_live_promote_sources(monkeypatch, tmp_path)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps({"generated_at_utc": "2026-05-08T00:00:00+00:00", "commits": [make_commit_entry("a" * 40)]}))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps({"generated_at_utc": "x", "file_changes": []}))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps({"events": []}))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.mcp.tools.substrate import substrate_readiness_report

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id="dag:test-readiness-ok",
        write_evidence_graph=False,
    )

    result = substrate_readiness_report()
    assert result["latest_refresh_id"] == "dag:test-readiness-ok"
    assert result["substrate_version"] is not None

    by_source = {s["source"]: s for s in result["sources"]}
    assert by_source["commits"]["kind"] == "stage"
    assert by_source["commits"]["status"] == "ok"
    assert by_source["commits"]["row_count"] == 1
    assert by_source["file_changes"]["status"] == "empty"
    assert by_source["symbols"]["status"] == "empty"

    counts = result["summary"]
    total = counts["ok"] + counts["empty"] + counts["unavailable"] + counts["error"]
    assert total == len(result["sources"])


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
                ended_at, duration_s, status, host, refresh_id
            )
            VALUES (
                'xtask', 'xtask:live:1', 'xtask_invocation', 'sinex',
                ['check'], TIMESTAMPTZ '2026-05-31 12:00:00+00',
                TIMESTAMPTZ '2026-05-31 12:02:00+00', 120.0, 'success',
                'sinnix-prime', 'r1'
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
                'xtask', 'xtask:live:stage:1', 'xtask:live:1', 'clippy',
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
                'xtask', 'xtask:live:stage:2', 'xtask:live:1', 'compile',
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
                'xtask', 'xtask:live:test:1', 'xtask:live:1',
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
                'xtask', 'xtask:live:test:2', 'xtask:live:1',
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
    )

    daily = machine_work_observation_daily(project="sinex", command_contains="check")
    assert daily["summary"]["row_count"] == 1
    assert daily["rows"][0]["observation_count"] == 1
    assert daily["rows"][0]["median_duration_s"] == 120.0

    stages = machine_work_stage_summary(stage_name="clippy")
    assert stages["rows"][0]["stage_name"] == "clippy"
    assert stages["rows"][0]["median_duration_s"] == 42.0

    tests = machine_work_test_summary(package="pkg")
    assert tests["rows"][0]["package"] == "pkg"
    assert {row["status"] for row in tests["rows"]} == {"pass", "fail"}

    slow_tests = machine_work_slow_tests(package="pkg", project="sinex")
    assert slow_tests["rows"][0]["test_name"] == "pkg::test_fail"
    assert slow_tests["rows"][0]["duration_s"] == 4.5

    stage_daily = machine_work_stage_daily(stage_name="compile", project="sinex")
    assert stage_daily["rows"][0]["stage_name"] == "compile"
    assert stage_daily["rows"][0]["success_count"] == 0

    failures = machine_work_failures(project="sinex", package="pkg")
    assert failures["rows"][0]["failure_kind"] == "test"
    assert failures["rows"][0]["source_id"] == "xtask:live:test:2"
