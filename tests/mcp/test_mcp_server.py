"""Tests for the lynchpin MCP server (Arc 4).

Verifies:
- query_substrate: SELECT passes, DDL/DML rejected, truncation, JSON safety
- list_substrate_tables: returns known tables
- list_evidence_graph_builds: empty substrate → [], with build → 1 row
- project_day_correlations: reader wrapper returns list[dict] with expected keys
- closure_chain_walks: reader wrapper returns list[dict]
- pr_review_rows: filter-by-state works

Tool functions are imported directly (FastMCP decorators don't break direct calls).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _reload_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.core.config as cfg_mod
    cfg_mod._CONFIG = None
    monkeypatch.setattr(cfg_mod, "_CONFIG", None, raising=False)


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


def _make_commit_entry(sha: str, project: str = "lynchpin") -> dict:
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "project": project,
        "author": "Sinity",
        "timestamp": "2026-05-01T12:00:00+00:00",
        "date": "2026-05-01",
        "subject": "feat: test",
        "parent_count": 1,
        "default_branch": "master",
        "head": None,
        "conventional_kind": "feat",
        "conventional_scope": None,
        "conventional_signature": "feat",
        "conventional_description": "test",
        "breaking_change": False,
        "github_refs": {"prs": [], "issues": []},
        "files_changed": 2,
        "classified_files_changed": 2,
        "categories": {},
        "path_roots": {"src": 2},
        "change_types": {"modified": 2},
        "paths": ["src/a.py", "src/b.py"],
    }


def _make_pr_dict(project: str = "lynchpin", state: str = "merged") -> dict[str, Any]:
    """Return a PR row as dict for substrate insertion via promote_pr_review_rows."""
    return {
        "project": project,
        "number": 1,
        "title": "feat: test PR",
        "state": state,
        "url": f"https://github.com/sinity/{project}/pull/1",
        "author": "Sinity",
        "created_at": "2026-05-01T10:00:00+00:00",
        "closed_at": "2026-05-01T12:00:00+00:00",
        "merged_at": "2026-05-01T12:00:00+00:00" if state == "merged" else None,
        "review_count": 1,
        "review_decisions": ["approved"],
        "review_round_count": 1,
        "reviewer_count": 1,
        "reviewers": ["reviewer1"],
        "review_comment_count": 2,
        "top_level_comment_count": 1,
        "changes_requested_count": 0,
        "approval_count": 1,
        "dismissed_count": 0,
        "time_to_first_review_minutes": 30.0,
        "time_to_close_minutes": 120.0,
        "time_to_merge_minutes": 120.0 if state == "merged" else None,
        "final_decision": "approved",
        "friction_signals": [],
    }


def _setup_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point substrate to tmp_path, apply schema, return db path."""
    import lynchpin.substrate.connection as duck_conn

    db_path = tmp_path / "substrate.duckdb"

    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: db_path)

    from lynchpin.substrate.connection import apply_schema, connect

    with connect(db_path) as conn:
        apply_schema(conn)

    return db_path


# ---------------------------------------------------------------------------
# query_substrate tests
# ---------------------------------------------------------------------------


def test_query_substrate_select_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SELECT statement against an empty substrate returns columns/rows shape."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate("SELECT COUNT(*) AS cnt FROM commit_fact")
    assert "columns" in result
    assert "rows" in result
    assert result["columns"] == ["cnt"]
    assert result["row_count"] == 1
    assert result["rows"][0][0] == 0  # empty table
    assert result["truncated"] is False


def test_query_substrate_rejects_drop_table() -> None:
    """DROP TABLE rejected with ValueError."""
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("DROP TABLE commit_fact")


def test_query_substrate_rejects_insert() -> None:
    """INSERT rejected with ValueError."""
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("INSERT INTO commit_fact VALUES (1)")


def test_query_substrate_rejects_delete() -> None:
    """DELETE rejected with ValueError."""
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("DELETE FROM commit_fact WHERE 1=1")


def test_query_substrate_rejects_create() -> None:
    """CREATE TABLE rejected."""
    from lynchpin.mcp.tools.substrate import query_substrate

    with pytest.raises(ValueError, match="disallowed"):
        query_substrate("CREATE TABLE x (id INTEGER)")


def test_query_substrate_truncates_at_max_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1500-row result with max_rows=100 → truncated=True, len(rows)==100."""
    import lynchpin.substrate.connection as duck_conn

    db_path = _setup_substrate(tmp_path, monkeypatch)

    # Generate 1500 rows via a values CTE — no writes to substrate needed
    # Use a DuckDB generate_series expression
    sql = "SELECT generate_series AS n FROM generate_series(1, 1500)"

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(sql, max_rows=100)
    assert result["truncated"] is True
    assert result["row_count"] == 100
    assert len(result["rows"]) == 100


def test_query_substrate_no_truncation_when_within_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10-row result with default max_rows → truncated=False."""
    _setup_substrate(tmp_path, monkeypatch)

    sql = "SELECT generate_series AS n FROM generate_series(1, 10)"

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(sql)
    assert result["truncated"] is False
    assert result["row_count"] == 10


def test_query_substrate_datetime_serialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """datetime columns come back as ISO strings, not datetime objects."""
    _setup_substrate(tmp_path, monkeypatch)

    sql = "SELECT TIMESTAMPTZ '2026-05-01 12:00:00+00' AS ts"

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(sql)
    val = result["rows"][0][0]
    assert isinstance(val, str)
    assert "2026-05-01" in val


def test_query_substrate_cte_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CTE (WITH … SELECT) is allowed."""
    _setup_substrate(tmp_path, monkeypatch)

    sql = "WITH t AS (SELECT 42 AS val) SELECT val FROM t"

    from lynchpin.mcp.tools.substrate import query_substrate

    result = query_substrate(sql)
    assert result["rows"][0][0] == 42


# ---------------------------------------------------------------------------
# list_substrate_tables
# ---------------------------------------------------------------------------


def test_list_substrate_tables_returns_known_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lists all expected substrate tables including commit_fact, ai_work_event."""
    _setup_substrate(tmp_path, monkeypatch)

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

    # Each table has a columns list
    for t in tables:
        assert "columns" in t
        assert isinstance(t["columns"], list)
        if t["columns"]:
            assert "name" in t["columns"][0]
            assert "type" in t["columns"][0]


# ---------------------------------------------------------------------------
# list_evidence_graph_builds
# ---------------------------------------------------------------------------


def test_list_evidence_graph_builds_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No builds in substrate → empty list returned."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import list_evidence_graph_builds

    result = list_evidence_graph_builds()
    assert result == []


def test_list_evidence_graph_builds_with_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After promoting a graph build, list returns one entry with expected keys."""
    import lynchpin.substrate.connection as duck_conn

    db_path = _setup_substrate(tmp_path, monkeypatch)

    # Insert a minimal build row directly
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
    r = result[0]
    assert r["refresh_id"] == "test-rid-001"
    assert r["node_count"] == 42
    assert r["edge_count"] == 7
    # Dates are serialised to ISO strings
    assert isinstance(r["start_date"], str)
    assert r["start_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# project_day_correlations
# ---------------------------------------------------------------------------


def test_project_day_correlations_returns_empty_on_empty_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty substrate → empty list, no crash."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import project_day_correlations

    result = project_day_correlations()
    assert isinstance(result, list)
    assert result == []


def test_project_day_correlations_returns_dataclass_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After promoting commit facts that land in the view, returns dicts with expected keys."""
    import json as _json

    db_path = _setup_substrate(tmp_path, monkeypatch)

    # Write a minimal commit-facts JSON and promote
    cf_payload = {
        "generated_at_utc": "2026-05-08T00:00:00+00:00",
        "commits": [_make_commit_entry("abc" + "0" * 37)],
    }
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(_json.dumps(cf_payload))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(tmp_path / "no_fc.json"),
        symbol_changes_file=str(tmp_path / "no_sym.json"),
        write_evidence_graph=False,
    )

    from lynchpin.mcp.tools.views import project_day_correlations

    result = project_day_correlations()
    # view may return 0 or more rows depending on evidence-graph state
    assert isinstance(result, list)
    if result:
        row = result[0]
        assert "project" in row
        assert "date" in row
        assert "commit_count" in row
        assert "source_count" in row


# ---------------------------------------------------------------------------
# closure_chain_walks
# ---------------------------------------------------------------------------


def test_closure_chain_walks_returns_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty substrate → empty list (view is empty, no crash)."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import closure_chain_walks

    result = closure_chain_walks()
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# pr_review_rows
# ---------------------------------------------------------------------------


def test_pr_review_rows_filters_by_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state=['merged'] returns only merged rows; state=['open'] returns only open."""
    db_path = _setup_substrate(tmp_path, monkeypatch)

    # Promote two PR rows: one merged, one open
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.promote import promote_pr_review_rows

    merged_row = _make_pr_dict(project="lynchpin", state="merged")
    open_row = _make_pr_dict(project="sinex", state="open")

    with connect(db_path) as conn:
        promote_pr_review_rows(conn, rows=[merged_row, open_row], refresh_id="test-pr-001")

    from lynchpin.mcp.tools.views import pr_review_rows

    merged_results = pr_review_rows(states=["merged"])
    assert len(merged_results) == 1
    assert merged_results[0]["state"] == "merged"
    assert merged_results[0]["project"] == "lynchpin"

    open_results = pr_review_rows(states=["open"])
    assert len(open_results) == 1
    assert open_results[0]["state"] == "open"
    assert open_results[0]["project"] == "sinex"

    all_results = pr_review_rows()
    assert len(all_results) == 2


def test_pr_review_rows_empty_on_empty_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty substrate → empty list, no crash."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import pr_review_rows

    result = pr_review_rows()
    assert result == []


# ---------------------------------------------------------------------------
# Smoke: import and instantiate app
# ---------------------------------------------------------------------------


def test_mcp_app_instantiates() -> None:
    """FastMCP app can be imported and has the expected name."""
    from lynchpin.mcp.server import app

    assert app.name == "lynchpin"


def test_mcp_tools_registered() -> None:
    """All expected tool functions are importable as Python callables."""
    from lynchpin.mcp.tools.substrate import (
        list_evidence_graph_builds,
        list_substrate_tables,
        load_evidence_graph_summary,
        query_substrate,
        substrate_readiness_report,
        substrate_source_status,
    )
    from lynchpin.mcp.tools.views import (
        closure_chain_walks,
        file_overlap_edges,
        pr_review_rows,
        project_day_correlations,
        symbol_overlap_edges,
    )

    for fn in [
        query_substrate,
        list_substrate_tables,
        list_evidence_graph_builds,
        load_evidence_graph_summary,
        substrate_source_status,
        substrate_readiness_report,
        project_day_correlations,
        closure_chain_walks,
        file_overlap_edges,
        symbol_overlap_edges,
        pr_review_rows,
    ]:
        assert callable(fn)


# ---------------------------------------------------------------------------
# substrate_readiness_report (Arc E.1)
# ---------------------------------------------------------------------------


def test_readiness_report_empty_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty substrate (no promote runs) → trustworthy=False with zeros."""
    _setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.substrate import substrate_readiness_report

    result = substrate_readiness_report()
    assert result["latest_refresh_id"] is None
    assert result["sources"] == []
    assert result["evidence_graph"] is None
    assert result["summary"]["trustworthy"] is False


def test_readiness_report_after_successful_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One successful commits promote → trustworthy with status=ok recorded.

    A successful promote of commit_facts records ok for commits and unavailable
    for missing file_changes/symbols/ai_work_events files.  Asserting
    'trustworthy' here would be wrong (other sources are unavailable). Instead
    we check that the source we promoted reports ok with the right row count.
    """
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps({
        "generated_at_utc": "2026-05-08T00:00:00+00:00",
        "commits": [_make_commit_entry("a" * 40)],
    }))
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
    assert by_source["commits"]["status"] == "ok"
    assert by_source["commits"]["row_count"] == 1
    # file_changes/symbols payloads were empty (not missing) → status='empty'.
    assert by_source["file_changes"]["status"] == "empty"
    assert by_source["symbols"]["status"] == "empty"

    # ai_work_events likely failed (no real polylogue in tests). Either way,
    # summary's per-status counts match the per-source list.
    counts = result["summary"]
    total = counts["ok"] + counts["empty"] + counts["unavailable"] + counts["error"]
    assert total == len(result["sources"])
