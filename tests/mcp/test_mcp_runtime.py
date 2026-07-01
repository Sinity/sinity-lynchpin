from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import setup_substrate


def test_collapsed_public_mcp_surface_has_eight_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.registry import PUBLIC_TOOL_NAMES
    from lynchpin.mcp.server import app

    tools = getattr(app._tool_manager, "_tools", {})

    assert set(tools) == set(PUBLIC_TOOL_NAMES)
    assert tuple(sorted(tools)) == tuple(sorted(PUBLIC_TOOL_NAMES))
    assert len(tools) == 8
    assert "query_substrate" not in tools
    assert "machine_metrics" not in tools
    assert "personal_daily_signals" not in tools


def test_legacy_modules_remain_importable_but_do_not_register_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.server import app
    from lynchpin.mcp.tools.substrate import query_substrate
    from lynchpin.mcp.tools.machine_status import machine_metrics

    assert callable(query_substrate)
    assert callable(machine_metrics)
    assert set(app._tool_manager._tools) == {
        "lynchpin_status",
        "lynchpin_catalog",
        "lynchpin_query",
        "lynchpin_evidence",
        "lynchpin_project",
        "lynchpin_personal",
        "lynchpin_machine",
        "lynchpin_ops",
    }


def test_lynchpin_status_self_check_reports_public_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_status

    result = lynchpin_status(view="self_check")

    assert result["ok"] is True
    data = result["data"]
    assert data["ok"] is True
    assert data["registered_tool_count"] == 8
    assert data["expected_tool_count"] == 8
    assert data["missing_public_tools"] == []
    assert data["unexpected_tools"] == []


def test_lynchpin_catalog_exposes_actions_and_legacy_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.public import lynchpin_catalog

    result = lynchpin_catalog(include_schema=True, include_legacy_map=True)

    assert result["ok"] is True
    data = result["data"]
    assert data["tool_count"] == 8
    tools = {tool["name"]: tool for tool in data["tools"]}
    assert "lynchpin_ops" in tools
    assert {action["name"] for action in tools["lynchpin_ops"]["actions"]} >= {
        "materialize",
        "chisel",
        "ai_backfill",
        "prune",
    }
    assert data["legacy_map"]["query_substrate"]["tool"] == "lynchpin_query"
    assert data["legacy_map"]["machine_metrics"]["tool"] == "lynchpin_machine"
    assert data["query_entities"]["commits"] == "commit_fact"
    assert data["source_contracts"]


def test_lynchpin_status_runtime_wraps_existing_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.public import lynchpin_status

    result = lynchpin_status(view="runtime")

    assert result["ok"] is True
    assert result["data"]["repo_root"]
    assert result["data"]["mcp"]["registered_tool_count"] == 8
    assert result["data"]["substrate"]["path"].endswith("substrate.duckdb")


def test_lynchpin_ops_materialize_dry_run_uses_manual_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    calls = []

    class Result:
        reason = "fixture"

        def to_json(self):
            return {"name": "reddit", "status": "blocked", "changed": False}

    def fake_ensure(name, *, window=None, budget="inline", force=False):
        calls.append((name, window, budget, force))
        return Result()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    from lynchpin.mcp.tools.public import lynchpin_ops

    result = lynchpin_ops(
        action="materialize",
        source="reddit",
        start="2026-06-01",
        end="2026-06-05",
        execute=False,
        force=True,
    )

    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert calls[0][0] == "reddit"
    assert calls[0][2] == "manual"
    assert [str(part) for part in calls[0][1]] == ["2026-06-01", "2026-06-06"]


def test_lynchpin_ops_materialize_execute_records_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    class Result:
        reason = "updated fixture"

        def to_json(self):
            return {"name": "reddit", "status": "updated", "changed": True}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda *args, **kwargs: Result())

    from lynchpin.core.freshness import latest_receipts
    from lynchpin.mcp.tools.public import lynchpin_ops

    result = lynchpin_ops(action="materialize", source="reddit", execute=True)

    assert result["ok"] is True
    assert result["data"]["dry_run"] is False
    assert result["data"]["receipt_id"].startswith("mcp:materialize:")
    receipts = latest_receipts(target="mcp_ops:materialize", limit=1)
    assert receipts[0]["receipt_id"] == result["data"]["receipt_id"]
