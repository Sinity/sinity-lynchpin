from __future__ import annotations

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
        "slices": {"view": "slices", "project": "alpha"},
        "audit": {"view": "audit", "project": "alpha"},
    }
    monkeypatch.setattr("lynchpin.mcp.tools.code_snapshots.code_snapshot_status", lambda: expected["status"])
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


def test_invalid_actions_return_structured_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_machine

    result = lynchpin_machine(action="not-real")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_action"
    assert "status" in result["choices"]


def test_lynchpin_status_readiness_does_not_forward_window_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_readiness() -> dict[str, object]:
        calls.append("called")
        return {"status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.substrate_readiness_report",
        fake_readiness,
    )

    from lynchpin.mcp.tools.public import lynchpin_status

    result = lynchpin_status(view="readiness", start="2026-07-01", end="2026-07-02")

    assert result["ok"] is True
    assert result["data"] == {"status": "ready"}
    assert calls == ["called"]


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


def test_lynchpin_machine_pressure_drops_unsupported_public_kwargs(
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

    assert result["ok"] is True
    assert result["data"] == {"summary": {"status": "ok"}}
    assert calls == [{"start": "2026-07-01", "end": "2026-07-02", "host": "sinnix-prime"}]


def test_project_and_evidence_routes_label_source_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lynchpin.mcp.tools.git_analysis.repo_recent_commits",
        lambda *, repo, limit=100: {"repo": repo, "commit_count": 0, "commits": []},
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.views.project_day_correlations",
        lambda **_kwargs: [],
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
            "rows": [], "refresh_id": "rid", "limit": 25, "offset": 50,
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
        return [{"refresh_id": "refreshed", "date": "2026-09-07"}]

    def read_meta(*, refresh_id, **_kwargs):
        return {"refresh_id": refresh_id or state["refresh_id"]}

    monkeypatch.setattr("lynchpin.mcp.tools.views.project_day_correlations", read_rows)
    monkeypatch.setattr("lynchpin.mcp.tools.public._project_day_timeline_meta", read_meta)
    from lynchpin.mcp.tools.public import lynchpin_evidence

    result = lynchpin_evidence(action="timeline", start="2026-09-07", end="2026-09-07")
    assert result["ok"] is True
    assert result["meta"]["refresh_id"] == "refreshed"
    assert result["data"][0]["refresh_id"] == result["meta"]["refresh_id"]


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
