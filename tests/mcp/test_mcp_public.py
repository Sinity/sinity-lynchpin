from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.mcp.conftest import setup_substrate


def test_lynchpin_query_sql_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": "sql", "sql": "SELECT COUNT(*) AS cnt FROM commit_fact"})

    assert result["ok"] is True
    assert result["meta"]["tool"] == "lynchpin_query"
    assert result["meta"]["action"] == "sql"
    assert result["meta"]["effect_mode"] == "read"
    assert result["data"]["columns"] == ["cnt"]
    assert result["data"]["row_count"] == 1


def test_lynchpin_query_rejects_mutating_sql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": "sql", "sql": "DROP TABLE commit_fact"})

    assert result["ok"] is False
    assert result["error_code"] == "query_error"


def test_lynchpin_query_sql_accepts_keyword_literal_and_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": "sql", "sql": "SELECT 'update' AS word -- delete\n"})
    assert result["ok"] is True
    assert result["data"]["rows"] == [["update"]]


def test_lynchpin_query_dsl_selects_entity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query(
        {
            "entity": "commits",
            "select": ["sha", "repo"],
            "where": {"repo": "lynchpin"},
            "limit": 5,
            "explain": True,
        }
    )

    assert result["ok"] is True
    assert result["meta"]["mode"] == "dsl"
    assert result["meta"]["tool"] == "lynchpin_query"
    assert result["meta"]["action"] == "dsl"
    assert "SELECT" in result["data"]["sql"]
    assert result["data"]["row_count"] == 0


@pytest.mark.parametrize(
    ("mode", "query"),
    [
        ("sql", {"sql": "SELECT 1 AS value"}),
        ("dsl", {"table": "commit_fact", "select": ["sha"]}),
    ],
)
def test_lynchpin_query_reports_served_source_gaps(
    mode: str, query: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO substrate_promotion_run "
            "(refresh_id, status, started_at, finished_at) VALUES "
            "('retained', 'degraded', TIMESTAMPTZ '2026-01-01 00:00:00+00', "
            "TIMESTAMPTZ '2026-01-01 00:01:00+00')"
        )
        conn.execute(
            "INSERT INTO substrate_source_status "
            "(refresh_id, source, kind, status, reason, row_count, window_start, "
            "window_end, recorded_at) VALUES "
            "('retained', 'commits', 'stage', 'ok', NULL, 3, "
            "DATE '2026-01-01', DATE '2026-01-03', TIMESTAMPTZ '2026-01-01 00:01:00+00'), "
            "('retained', 'sessions', 'stage', 'unavailable', 'input missing', NULL, "
            "NULL, NULL, TIMESTAMPTZ '2026-01-01 00:01:00+00')"
        )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "blocked", "reason": "inputs changed"},
    )

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": mode, **query})

    assert result["ok"] is True
    assert result["data"]["serving"]["refresh_id"] == "retained"
    assert result["data"]["freshness"]["status"] == "blocked"
    assert result["data"]["freshness"]["serving_source_status_refresh_id"] == "retained"
    statuses = result["data"]["freshness"]["serving_source_status"]
    assert all(
        datetime.fromisoformat(row["recorded_at"]).astimezone(timezone.utc)
        == datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        for row in statuses
    )
    assert [{key: value for key, value in row.items() if key != "recorded_at"} for row in statuses] == [
        {
            "source": "commits", "kind": "stage", "status": "ok", "reason": None,
            "row_count": 3, "window_start": "2026-01-01", "window_end": "2026-01-03",
        },
        {
            "source": "sessions", "kind": "stage", "status": "unavailable",
            "reason": "input missing", "row_count": None, "window_start": None,
            "window_end": None,
        },
    ]


def test_lynchpin_query_dsl_reports_truncation_at_requested_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE query_fixture (value INTEGER)")
        conn.execute("INSERT INTO query_fixture VALUES (1), (2), (3)")

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query(
        {"table": "query_fixture", "select": ["value"], "order_by": "value", "limit": 2}
    )

    assert result["ok"] is True
    assert result["data"]["rows"] == [[1], [2]]
    assert result["data"]["row_count"] == 2
    assert result["data"]["truncated"] is True
    assert result["data"]["next_offset"] is None


def test_lynchpin_query_dsl_continues_beyond_old_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE query_fixture AS SELECT n FROM range(12005) AS t(n)")
        conn.execute("INSERT OR REPLACE INTO substrate_meta VALUES ('publication_id', 'query-publication')")
        conn.execute("INSERT OR REPLACE INTO substrate_meta VALUES ('publication_at', '2026-01-01T00:00:00+00:00')")
    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "ready"},
    )
    from lynchpin.mcp.tools.public import lynchpin_query

    query = {"table": "query_fixture", "select": ["n"], "order_by": "n", "limit": 20000, "max_rows": 4000}
    seen: list[int] = []
    offset = 0
    while True:
        result = lynchpin_query({**query, "offset": offset, **({"expected_publication_id": "query-publication"} if offset else {})})
        assert result["ok"] is True, result
        data = result["data"]
        seen.extend(row[0] for row in data["rows"])
        if data["next_offset"] is None:
            break
        offset = data["next_offset"]
    assert seen == list(range(12005))


def test_lynchpin_query_dsl_null_and_invalid_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE query_fixture (value INTEGER)")
        conn.execute("INSERT INTO query_fixture VALUES (NULL), (1)")
    from lynchpin.mcp.tools.public import lynchpin_query

    matched = lynchpin_query({"table": "query_fixture", "select": ["value"], "where": {"value": None}})
    assert matched["ok"] is True
    assert matched["data"]["rows"] == [[None]]
    rejected = lynchpin_query({"table": "query_fixture", "where": {"value": None}, "offest": 1})
    assert rejected["error_code"] == "invalid_argument"
    assert lynchpin_query({"table": "query_fixture", "time": []})["error_code"] == "invalid_time"
    assert lynchpin_query({"table": "query_fixture", "order_by": []})["error_code"] == "invalid_order_by"


def test_mcp_client_route_rejects_ignored_query_and_personal_filters() -> None:
    from lynchpin.mcp.server import app
    from lynchpin.mcp.tools import public as _public  # noqa: F401 - registers public tools

    async def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        content = await app.call_tool(name, arguments)
        return json.loads(content[0].text)

    invalid_query = asyncio.run(call("lynchpin_query", {"spec": {"table": "commit_fact", "offest": 3}}))
    assert invalid_query["error_code"] == "invalid_argument"
    invalid_filter = asyncio.run(call("lynchpin_personal", {"action": "communications", "source": "gmail"}))
    assert invalid_filter["error_code"] == "invalid_argument"


@pytest.mark.parametrize(
    ("mode", "query"),
    [
        ("sql", {"sql": "SELECT 1 AS value"}),
        ("dsl", {"table": "commit_fact", "select": ["sha"], "limit": 2}),
    ],
)
def test_lynchpin_query_surfaces_blocked_materialization_caveat(
    mode: str,
    query: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    def record_blocked(**_kwargs: object) -> dict[str, object]:
        from lynchpin.mcp.tools._utils import _record_materialization_caveat

        _record_materialization_caveat(
            {"caller": "query_substrate", "status": "blocked", "reason": "fixture"}
        )
        return {"status": "blocked", "reason": "fixture"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        record_blocked,
    )

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": mode, **query})

    assert result["ok"] is True
    assert result["meta"]["materialization_caveats"] == [
        {"caller": "query_substrate", "status": "blocked", "reason": "fixture"}
    ]


@pytest.mark.parametrize(
    ("mode", "query"),
    [
        ("sql", {"sql": "SELECT 1 AS value"}),
        ("dsl", {"table": "commit_fact", "select": ["sha"], "limit": 2}),
    ],
)
def test_lynchpin_query_rejects_mismatched_expected_refresh(
    mode: str,
    query: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO substrate_promotion_run "
            "(refresh_id, status, started_at, finished_at) VALUES "
            "('served-generation', 'ok', TIMESTAMPTZ '2026-01-01 00:00:00+00', "
            "TIMESTAMPTZ '2026-01-01 00:01:00+00')"
        )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "ready"},
    )

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query(
        {"mode": mode, **query, "expected_refresh_id": "different-generation"}
    )

    assert result["ok"] is False
    assert result["error_code"] == "refresh_mismatch"
    assert result["details"] == {
        "expected_refresh_id": "different-generation",
        "actual_refresh_id": "served-generation",
        "serving_kind": "canonical",
    }


@pytest.mark.parametrize("mode", ["sql", "dsl"])
def test_lynchpin_query_pins_publication_even_when_promotion_is_unchanged(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    import duckdb

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO substrate_promotion_run "
            "(refresh_id, status, started_at, finished_at) VALUES "
            "('same-promotion', 'ok', TIMESTAMPTZ '2026-01-01 00:00:00+00', "
            "TIMESTAMPTZ '2026-01-01 00:01:00+00')"
        )
        conn.execute("INSERT OR REPLACE INTO substrate_meta VALUES ('publication_id', 'new-publication')")
        conn.execute("INSERT OR REPLACE INTO substrate_meta VALUES ('publication_at', '2026-01-01T00:02:00+00:00')")
    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "ready"},
    )
    from lynchpin.mcp.tools.public import lynchpin_query

    query: dict[str, object] = (
        {"mode": "sql", "sql": "SELECT 1 AS value"}
        if mode == "sql" else {"mode": "dsl", "table": "commit_fact", "select": ["sha"]}
    )
    mismatch = lynchpin_query({
        **query, "expected_refresh_id": "same-promotion",
        "expected_publication_id": "old-publication",
    })
    assert mismatch["ok"] is False
    assert mismatch["error_code"] == "publication_mismatch"
    assert mismatch["details"] == {
        "expected_publication_id": "old-publication",
        "actual_publication_id": "new-publication",
    }
    matched = lynchpin_query({**query, "expected_publication_id": "new-publication"})
    assert matched["ok"] is True
    assert matched["data"]["serving"]["publication_id"] == "new-publication"


def test_lynchpin_project_routes_repo_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="repos")

    assert result["ok"] is True
    assert result["meta"]["tool"] == "lynchpin_project"
    assert result["meta"]["action"] == "repos"
    assert result["meta"]["route"].endswith(".repo_names")
    assert isinstance(result["data"], list)


def test_lynchpin_project_dispatches_every_declared_snapshot_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    expected = {
        "status": {"view": "status"},
        "runs": [{"project": "alpha"}],
        "slices": {"view": "slices", "project": "alpha"},
        "audit": {"view": "audit", "project": "alpha"},
    }
    monkeypatch.setattr("lynchpin.mcp.tools.code_snapshots.code_snapshot_status", lambda: expected["status"])
    monkeypatch.setattr("lynchpin.mcp.tools.code_snapshots.list_code_snapshot_runs", lambda *, project=None: [{"project": project}])
    monkeypatch.setattr("lynchpin.mcp.tools.code_snapshots.list_code_snapshot_slices", lambda *, project=None: {"view": "slices", "project": project})
    monkeypatch.setattr("lynchpin.mcp.tools.code_snapshots.code_snapshot_audit", lambda *, project=None: {"view": "audit", "project": project})

    from lynchpin.mcp.registry import public_action_spec
    from lynchpin.mcp.tools.public import lynchpin_project

    spec = public_action_spec("lynchpin_project", "snapshots")
    assert spec is not None
    assert set(spec.views) == set(expected)

    for view in spec.views:
        result = lynchpin_project(action="snapshots", view=view, project="alpha")
        assert result["ok"] is True
        assert result["data"] == expected[view]


@pytest.mark.parametrize(
    ("tool", "action", "view", "module", "function", "expected_kwargs"),
    [
        ("project", "velocity", "throughput", "velocity", "engineering_throughput", {"granularity": "week"}),
        ("project", "velocity", "daily", "velocity", "engineering_throughput", {"granularity": "day"}),
        ("project", "velocity", "weekly", "velocity", "engineering_throughput", {"granularity": "week"}),
        ("project", "change_kinds", "conventional", "change", "conventional_commits", {}),
        ("project", "change_kinds", "breaking", "change", "breaking_changes", {}),
        ("project", "change_kinds", "ai", "change", "commit_kind_attribution", {}),
        ("project", "change_kinds", "attribution", "change", "commit_kind_attribution", {}),
        ("personal", "activity", "daily", "personal", "activity_content_daily", {}),
        ("personal", "activity", "focus", "personal", "focus_daily", {}),
        ("personal", "activity", "titles", "personal", "activity_title_usage", {}),
        ("personal", "activity", "unmatched", "personal", "activity_unmatched_titles", {}),
        ("personal", "activity", "coverage", "personal", "activity_content_coverage", {}),
        ("personal", "web", "daily", "personal", "web_daily", {}),
        ("personal", "web", "provenance", "personal", "webhistory_provenance", {}),
        ("personal", "web", "takeout", "personal", "google_takeout_events", {}),
        ("personal", "operator", "rhythm", "personal", "operator_rhythm", {}),
        ("personal", "operator", "readiness", "personal", "operator_retrospective_readiness", {}),
    ],
)
def test_catalog_view_reaches_its_executable_route(
    tool: str, action: str, view: str, module: str, function: str,
    expected_kwargs: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.mcp.registry import public_action_spec
    from lynchpin.mcp.tools import public

    spec = public_action_spec(f"lynchpin_{tool}", action)
    assert spec is not None and view in spec.views
    calls: list[dict[str, object]] = []

    def leaf(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"reached": function}

    monkeypatch.setattr(f"lynchpin.mcp.tools.{module}.{function}", leaf)
    route = public.lynchpin_project if tool == "project" else public.lynchpin_personal
    dates = {"start": "2026-01-01", "end": "2026-01-02"} if "start" in spec.parameters else {}
    result = route(action=action, view=view, **dates)

    assert result["ok"] is True
    assert result["data"] == {"reached": function}
    assert calls and all(calls[0].get(key) == value for key, value in expected_kwargs.items())


@pytest.mark.parametrize(
    ("tool", "action", "view"),
    [
        ("project", "velocity", "unlisted"),
        ("project", "snapshots", "unlisted"),
        ("personal", "activity", "buckets"),
        ("personal", "web", "domains"),
        ("personal", "operator", "verify_vs_edit_ratio"),
    ],
)
def test_unknown_public_view_is_typed_error(tool: str, action: str, view: str) -> None:
    from lynchpin.mcp.tools import public

    route = public.lynchpin_project if tool == "project" else public.lynchpin_personal
    result = route(action=action, view=view)
    assert result["ok"] is False
    assert result["error_code"] == "invalid_view"


def test_nested_unknown_view_payload_is_not_wrapped_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools.public import lynchpin_personal

    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.communication",
        lambda **_kwargs: {"error": "unknown view 'missing'. choices: events, daily"},
    )
    result = lynchpin_personal(action="communications", view="missing")
    assert result["ok"] is False
    assert result["error_code"] == "invalid_view"


def test_personal_health_default_preserves_requested_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools.public import lynchpin_personal

    calls: list[tuple[str, str]] = []

    def daily(start: str, end: str) -> list[dict[str, str]]:
        calls.append((start, end))
        return [{"date": start}]

    monkeypatch.setattr("lynchpin.mcp.tools.health.health_daily_summary", daily)
    result = lynchpin_personal(action="health", start="2026-01-01", end="2026-01-02")
    assert result["ok"] is True
    assert result["data"] == [{"date": "2026-01-01"}]
    assert calls == [("2026-01-01", "2026-01-02")]


def test_personal_health_requires_dates_by_default_and_rejects_dates_for_substrate_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools.public import lynchpin_personal

    calls: list[str] = []
    monkeypatch.setattr("lynchpin.mcp.tools.health.health_trend", lambda: calls.append("trend") or {"kind": "substrate"})

    default = lynchpin_personal(action="health")
    dated_trend = lynchpin_personal(action="health", view="trend", start="2026-01-01", end="2026-01-02")
    explicit_trend = lynchpin_personal(action="health", view="trend")

    assert default["ok"] is False and default["error_code"] == "missing_argument"
    assert dated_trend["ok"] is False and dated_trend["error_code"] == "invalid_request"
    assert explicit_trend["ok"] is True and explicit_trend["data"] == {"kind": "substrate"}
    assert calls == ["trend"]


def test_invalid_actions_return_structured_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_machine

    result = lynchpin_machine(action="not-real")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_action"
    assert "status" in result["choices"]


def test_public_note_search_and_read_route_to_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools import owner_access
    from lynchpin.mcp.tools.public import lynchpin_personal

    calls: list[dict[str, object]] = []

    def notes(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"source": "dendron", "status": "complete", "notes": [{"path": "entry.md"}]}

    monkeypatch.setattr(owner_access, "notes", notes)
    search = lynchpin_personal(action="notes", query="entry", offset=201, limit=20)
    read = lynchpin_personal(action="notes", view="read", path="entry.md")

    assert search["ok"] is True
    assert search["meta"]["action"] == "notes"
    assert search["data"]["notes"][0]["path"] == "entry.md"
    assert read["ok"] is True
    assert calls == [
        {"view": "search", "query": "entry", "path": None, "offset": 201, "limit": 20},
        {"view": "read", "query": "", "path": "entry.md", "offset": 0, "limit": 100},
    ]
    assert lynchpin_personal(action="notes", view="read")["error_code"] == "missing_argument"
    assert lynchpin_personal(action="notes", view="search", path="entry.md")["error_code"] == "invalid_argument"


def test_public_owner_failures_are_not_success_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools import owner_access
    from lynchpin.mcp.tools.public import lynchpin_machine, lynchpin_personal

    monkeypatch.setattr(
        owner_access, "notes",
        lambda **_kwargs: {"error": "FileNotFoundError", "message": "missing", "source": "dendron"},
    )
    monkeypatch.setattr(
        owner_access, "agentctl_job",
        lambda **_kwargs: {"error": "LookupError", "message": "missing", "source": "agentctl"},
    )
    assert lynchpin_personal(action="notes", view="read", path="missing.md")["ok"] is False
    assert lynchpin_machine(action="job", job_id="123")["ok"] is False
    assert lynchpin_machine(action="job")["error_code"] == "missing_argument"


def test_public_agentctl_job_detail_preserves_owner_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.mcp.tools import owner_access
    from lynchpin.mcp.tools.public import lynchpin_machine

    monkeypatch.setattr(
        owner_access, "agentctl_job",
        lambda *, job_id: {"job_id": job_id, "result": {"phase": "passed"}, "source": "agentctl"},
    )
    result = lynchpin_machine(action="job", job_id="123")
    assert result["ok"] is True
    assert result["data"]["job_id"] == "123"
    assert result["meta"]["route"] == "lynchpin.mcp.tools.owner_access.agentctl_job"


def test_lynchpin_status_readiness_forwards_window_to_analysis_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_readiness(start: str | None = None, end: str | None = None) -> dict[str, object]:
        calls.append((start, end))
        return {"requested_window": {"start": start, "end": end}}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.analysis_readiness",
        fake_readiness,
    )

    from lynchpin.mcp.tools.public import lynchpin_status

    result = lynchpin_status(view="readiness", start="2026-07-01", end="2026-07-02")

    assert result["ok"] is True
    assert result["data"]["requested_window"] == {"start": "2026-07-01", "end": "2026-07-02"}
    assert calls == [("2026-07-01", "2026-07-02")]


def test_lynchpin_status_snapshot_returns_compact_orientation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Row:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_json(self) -> dict[str, object]:
            return {
                "name": self.name,
                "status": "ready",
                "reason": "test",
                "source_high_water": {
                    "row_count": 3,
                    "first_date": "2026-07-01",
                    "last_date": "2026-07-02",
                },
                "coverage": {"relation": "covers"},
            }

    monkeypatch.setattr(
        "lynchpin.materialization.audit_materialization",
        lambda: [Row("polylogue"), Row("evidence_graph_substrate"), Row("unrelated")],
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.runtime.mcp_runtime_status",
        lambda: {"repo": {"branch": "master"}},
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.git_analysis.repo_recent_commits",
        lambda *, repo, limit=5: {"repo": repo, "commit_count": 0, "commits": []},
    )

    from lynchpin.mcp.tools.public import lynchpin_status

    result = lynchpin_status(view="snapshot", start="2026-07-01", end="2026-07-02")

    assert result["ok"] is True
    assert result["meta"]["action"] == "snapshot"
    assert result["data"]["kind"] == "situation_snapshot"
    assert result["data"]["window"] == {"start": "2026-07-01", "end": "2026-07-02"}
    assert [row["name"] for row in result["data"]["materialization"]] == [
        "polylogue",
        "evidence_graph_substrate",
    ]
    assert set(result["data"]["recent_commits"]) == {
        "polylogue",
        "sinex",
        "sinity-lynchpin",
    }


def test_lynchpin_machine_pressure_rejects_unsupported_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_pressure_report(*, start=None, end=None, host=None) -> dict[str, object]:
        calls.append({"start": start, "end": end, "host": host})
        return {"summary": {"status": "ok"}}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine_status.machine_pressure_report",
        fake_pressure_report,
    )

    from lynchpin.mcp.tools.public import lynchpin_machine

    result = lynchpin_machine(
        action="pressure",
        start="2026-07-01",
        end="2026-07-02",
        host="sinnix-prime",
        limit=5,
    )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_argument"
    assert calls == []


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"action": "communications", "source": "gmail"}, "invalid_argument"),
        ({"action": "communications", "query": "update"}, "invalid_argument"),
        ({"action": "web", "view": "daily", "query": "update"}, "invalid_argument"),
        ({"action": "web", "view": "daily", "limit": 5}, "invalid_argument"),
        ({"action": "reports", "start": "2026-01-01"}, "invalid_argument"),
        ({"action": "health", "view": "typo"}, "invalid_view"),
    ],
)
def test_personal_router_rejects_ignored_filters(kwargs: dict[str, object], expected_code: str) -> None:
    from lynchpin.mcp.tools.public import lynchpin_personal

    result = lynchpin_personal(**kwargs)
    assert result["ok"] is False
    assert result["error_code"] == expected_code


def test_project_and_evidence_routes_label_source_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lynchpin.mcp.tools.git_analysis.repo_recent_commits",
        lambda *, repo, limit=100: {"repo": repo, "commit_count": 0, "commits": []},
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.views.project_day_correlations_page",
        lambda **_kwargs: {
            "rows": [], "refresh_id": "rid", "publication_id": "pub", "limit": 3,
            "offset": 0, "returned_count": 0, "truncated": False, "next_offset": None,
        },
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.public._project_day_timeline_meta",
        lambda **_kwargs: {
            "source_mode": "substrate",
            "refresh_id": "rid",
            "coverage_start": "2026-06-01",
            "coverage_end": "2026-06-30",
            "coverage_row_count": 2,
            "matched_row_count": 0,
            "freshness_warning": "requested end exceeds materialized project-day correlation coverage",
        },
    )

    from lynchpin.mcp.tools.public import lynchpin_evidence, lynchpin_project

    commits = lynchpin_project(action="commits", project="polylogue", limit=3)
    timeline = lynchpin_evidence(
        action="timeline",
        project="polylogue",
        start="2026-07-01",
        end="2026-07-02",
        limit=3,
    )

    assert commits["ok"] is True
    assert commits["meta"]["source_mode"] == "live_git"
    assert timeline["ok"] is True
    assert timeline["meta"]["source_mode"] == "substrate"
    assert timeline["meta"]["coverage_end"] == "2026-06-30"
    assert timeline["meta"]["matched_row_count"] == 0
    assert "freshness_warning" in timeline["meta"]


def test_claims_route_passes_pagination_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_analysis_evidence(**kwargs):
        calls.append(kwargs)
        return {
            "rows": [], "refresh_id": "rid", "publication_id": "pub",
            "limit": 25, "offset": 50,
            "has_more": False, "next_offset": None,
        }

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.analysis_evidence",
        fake_analysis_evidence,
    )

    from lynchpin.mcp.tools.public import lynchpin_evidence

    result = lynchpin_evidence(action="claims", refresh_id="rid", limit=25, offset=50)

    assert result["ok"] is True
    assert result["data"] == []
    assert result["meta"]["refresh_id"] == "rid"
    assert result["meta"]["publication_id"] == "pub"
    assert result["meta"]["next_offset"] is None
    assert calls == [{
        "view": "claims", "refresh_id": "rid", "limit": 25, "offset": 50,
    }]


def test_timeline_metadata_observes_the_generation_returned_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"refresh_id": "old"}

    def read_rows(**_kwargs):
        state["refresh_id"] = "refreshed"
        return {
            "rows": [{"refresh_id": "refreshed", "date": "2026-09-07"}],
            "refresh_id": "refreshed", "publication_id": "pub", "limit": 100,
            "offset": 0, "returned_count": 1, "truncated": False, "next_offset": None,
        }

    def read_meta(*, refresh_id, **_kwargs):
        return {"refresh_id": refresh_id or state["refresh_id"]}

    monkeypatch.setattr("lynchpin.mcp.tools.views.project_day_correlations_page", read_rows)
    monkeypatch.setattr("lynchpin.mcp.tools.public._project_day_timeline_meta", read_meta)
    from lynchpin.mcp.tools.public import lynchpin_evidence

    result = lynchpin_evidence(action="timeline", start="2026-09-07", end="2026-09-07")
    assert result["ok"] is True
    assert result["meta"]["refresh_id"] == "refreshed"
    assert result["data"][0]["refresh_id"] == result["meta"]["refresh_id"]


def test_evidence_routes_forward_page_and_publication_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def timeline_page(**kwargs):
        calls.append(kwargs)
        return {
            "rows": [{"refresh_id": "rid", "date": "2026-05-01"}],
            "refresh_id": "rid", "publication_id": "pub", "limit": 1,
            "offset": 1, "returned_count": 1, "truncated": True, "next_offset": 2,
        }

    def claim_page(**kwargs):
        calls.append(kwargs)
        return {
            "refresh_id": "rid", "publication_id": "pub", "claim_id": "claim:1",
            "evidence_nodes": [{"id": "node:2"}], "evidence_edges": [],
            "returned_count": 1, "truncated": False, "next_offset": None,
        }

    monkeypatch.setattr("lynchpin.mcp.tools.views.project_day_correlations_page", timeline_page)
    monkeypatch.setattr("lynchpin.mcp.tools.substrate.analysis_evidence", claim_page)
    monkeypatch.setattr("lynchpin.mcp.tools.public._project_day_timeline_meta", lambda **_kwargs: {})
    from lynchpin.mcp.tools.public import lynchpin_evidence

    timeline = lynchpin_evidence(
        action="timeline", refresh_id="rid", limit=1, offset=1,
        expected_publication_id="pub",
    )
    claim = lynchpin_evidence(
        action="claim_evidence", claim_id="claim:1", refresh_id="rid", limit=1,
        offset=1, expected_publication_id="pub",
    )
    assert timeline["data"] == [{"refresh_id": "rid", "date": "2026-05-01"}]
    assert timeline["meta"]["returned_count"] == 1
    assert timeline["meta"]["truncated"] is True
    assert timeline["meta"]["next_offset"] == 2
    assert claim["meta"]["returned_count"] == 1
    assert calls[0]["limit"] == calls[1]["limit"] == 1
    assert calls[0]["offset"] == calls[1]["offset"] == 1
    assert calls[0]["expected_publication_id"] == calls[1]["expected_publication_id"] == "pub"


def test_project_github_forwards_page_and_publication_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def list_prs(**kwargs):
        calls.append(kwargs)
        return {"prs": [], "returned_count": 0, "truncated": False, "total": None}

    monkeypatch.setattr("lynchpin.mcp.tools.github.list_github_prs", list_prs)
    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(
        action="github", project="lynchpin", view="prs", limit=1, offset=3,
        expected_publication_id="pub",
    )
    assert result["ok"] is True
    assert calls == [{
        "project": "lynchpin", "limit": 1, "offset": 3,
        "expected_publication_id": "pub",
    }]
    detail = lynchpin_project(
        action="github", project="lynchpin", view="issue", number=1, offset=1,
    )
    assert detail["ok"] is False
    assert detail["error_code"] == "invalid_request"


def test_blocked_materialization_surfaces_as_response_caveat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lynchpin-zoz: a routed tool call that reads through an unmaterialized
    substrate window must say so in its own response, not just return
    ok=true with whatever partial/empty data the query happens to produce.

    setup_substrate() gives an empty, freshly-schema'd DuckDB with no
    evidence_graph_build rows at all, so any window is guaranteed
    unmaterialized — exactly the condition the original bug report hit.
    """
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_evidence

    result = lynchpin_evidence(action="timeline", start="2020-01-01", end="2020-01-05")

    assert result["ok"] is True
    caveats = result["meta"].get("materialization_caveats")
    assert caveats, "expected a materialization caveat for an unmaterialized window"
    assert caveats[0]["caller"] == "project_day_correlations"
    assert caveats[0]["status"] == "blocked"
    assert caveats[0]["window"] == ["2020-01-01", "2020-01-06"]


def test_ready_materialization_has_no_caveat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool call that never touches an unmaterialized window must not carry
    a stray materialization_caveats key — the ContextVar collecting them is
    reset per call, not leaking across requests or appearing when unused."""
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="repos")

    assert result["ok"] is True
    assert "materialization_caveats" not in result["meta"]
