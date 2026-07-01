from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import setup_substrate


def test_lynchpin_query_sql_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_query

    result = lynchpin_query({"mode": "sql", "sql": "SELECT COUNT(*) AS cnt FROM commit_fact"})

    assert result["ok"] is True
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
    assert "SELECT" in result["data"]["sql"]
    assert result["data"]["row_count"] == 0


def test_lynchpin_project_routes_repo_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="repos")

    assert result["ok"] is True
    assert isinstance(result["data"], list)


def test_invalid_actions_return_structured_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_machine

    result = lynchpin_machine(action="not-real")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_action"
    assert "status" in result["choices"]

