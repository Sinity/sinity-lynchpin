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


def test_lynchpin_project_routes_repo_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="repos")

    assert result["ok"] is True
    assert result["meta"]["tool"] == "lynchpin_project"
    assert result["meta"]["action"] == "repos"
    assert result["meta"]["route"].endswith(".repo_names")
    assert isinstance(result["data"], list)


def test_lynchpin_project_routes_snapshot_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)
    root = tmp_path / "snapshots"
    project_dir = root / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "alpha-snapshot-audit.json").write_text(
        '{"project":"alpha","status":"ok","open_first":[]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.sources.code_snapshots.code_snapshots_path", lambda project=None: root / project if project else root)

    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="snapshots", view="audit", project="alpha")

    assert result["ok"] is True
    assert result["meta"]["tool"] == "lynchpin_project"
    assert result["meta"]["action"] == "snapshots"
    assert result["data"]["audit_count"] == 1
    assert result["data"]["audits"][0]["project"] == "alpha"


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
